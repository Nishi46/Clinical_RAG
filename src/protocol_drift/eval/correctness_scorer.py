"""Correctness + faithfulness scorers -- S3-07.

T1 scores via exact_match_score -- no model call, no cost, since T1's
answers are auto-generated registry facts (S3-03) a normalized
string/duration comparison can grade directly. T2 correctness and
faithfulness both need the judge model (configs/models.yaml's `judge`
entry), wired through generation.answer.cached_generate so judge calls are
themselves traced and cached on prompt_hash -- a judge re-run over
unchanged answers costs zero new compute, same contract S3-06 established
for the primary generation call. Faithfulness costs one claim-extraction
call plus one grounding call per claim, roughly matching the "~6 calls per
eval question" budget in project_plan.md's appendix (1 answer + 1
correctness judge + 1 claim-extraction judge + ~N per-claim grounding
judges).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from protocol_drift.eval.models import EvalQuestion
from protocol_drift.generation.answer import RetrievedChunk, cached_generate
from protocol_drift.normalize.text import (
    contains_as_whole_token,
    extract_durations_in_months,
    normalize_text,
)
from protocol_drift.trace.store import TraceStore

logger = logging.getLogger(__name__)

# Mirrors configs/models.yaml's `judge` entry (same model as generation for
# now, per that config's own comment) -- same literal-constant convention
# as generation/answer.py's DEFAULT_MODEL_NAME/DEFAULT_MODEL_DIGEST.
DEFAULT_JUDGE_MODEL_NAME = "llama3.1:latest"
DEFAULT_JUDGE_MODEL_DIGEST = (
    "sha256:46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e"
)

_VALID_SCORES = (0.0, 0.5, 1.0)
_SCORE_PATTERN = re.compile(r"SCORE:\s*(0\.5|0|1)(?:\.0)?", re.IGNORECASE)

# A leading bullet ("- ", "* ") or numbered-list marker ("1. ", "2) ") --
# requires whitespace right after the marker, so a claim that happens to
# start with a bare number as content (e.g. "24 months is the timeframe.")
# is left untouched rather than having "24 " mistaken for "24." + strip.
_LIST_MARKER_PATTERN = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)")


def exact_match_score(generated: str, gold: str) -> bool:
    """T1 scoring: no model call. A recognized duration in `gold` matches
    if the same duration (in any equivalent unit) appears anywhere in
    `generated` -- the "24 months" vs. "2 years" case
    discrepancy_definition.md names as a match, not a divergence. Anything
    else falls back to a normalized, word-boundary substring check (so a
    full-sentence generated answer can still contain a short exact gold
    value like "Phase 2" or "6")."""
    gold_durations = extract_durations_in_months(gold)
    if gold_durations:
        return bool(gold_durations & extract_durations_in_months(generated))

    normalized_gold = normalize_text(gold)
    if not normalized_gold:
        return False
    return contains_as_whole_token(normalized_gold, normalize_text(generated))


def _parse_score(response_text: str) -> float | None:
    match = _SCORE_PATTERN.search(response_text)
    if match is None:
        return None
    value = float(match.group(1))
    return value if value in _VALID_SCORES else None


def judged_correctness(
    question: EvalQuestion,
    generated_answer: str,
    gold_notes: str,
    query_id: int,
    store: TraceStore,
    model: str = DEFAULT_JUDGE_MODEL_NAME,
    digest: str = DEFAULT_JUDGE_MODEL_DIGEST,
) -> tuple[float | None, str]:
    """T2 scoring: prompts the judge for a 0/0.5/1 correctness score plus a
    one-sentence justification. Parses defensively -- an unparseable
    response gets one retry with a clarifying nudge appended (a plain
    retry of the identical prompt would just hit the cache and reproduce
    the same unparseable text), then logs and returns `(None, ...)` rather
    than crashing a full eval run. Returns (score, judge_response_text) so
    the justification is always available for docs/judge_calibration.md
    even when the score itself doesn't parse."""
    prompt = _correctness_prompt(question, generated_answer, gold_notes)
    response_text, _, _ = cached_generate(prompt, query_id, store, model, digest)
    score = _parse_score(response_text)

    if score is None:
        retry_prompt = (
            prompt + "\n\n(Your previous response didn't match the required format. "
            "Please respond in exactly the format requested.)"
        )
        response_text, _, _ = cached_generate(retry_prompt, query_id, store, model, digest)
        score = _parse_score(response_text)

    if score is None:
        logger.warning(
            "judged_correctness: unparseable judge response for question %s: %r",
            question.question_id,
            response_text,
        )
    return score, response_text


def _correctness_prompt(question: EvalQuestion, generated_answer: str, gold_notes: str) -> str:
    return (
        "You are grading a clinical-trial question-answering system. Score the ANSWER "
        "against the REFERENCE NOTES on a scale of 0, 0.5, or 1: "
        "1 = fully correct, 0.5 = partially correct, 0 = incorrect.\n\n"
        f"QUESTION: {question.question_text}\n\n"
        f"ANSWER: {generated_answer}\n\n"
        f"REFERENCE NOTES: {gold_notes}\n\n"
        "Respond in exactly this format:\n"
        "SCORE: <0, 0.5, or 1>\n"
        "JUSTIFICATION: <one sentence>"
    )


def extract_claims(
    generated_answer_text: str,
    query_id: int,
    store: TraceStore,
    model: str = DEFAULT_JUDGE_MODEL_NAME,
    digest: str = DEFAULT_JUDGE_MODEL_DIGEST,
) -> list[str]:
    """Prompts the judge to split an answer into atomic, independently
    verifiable factual claims -- one per line, no numbering."""
    prompt = (
        "Split the following ANSWER into a list of atomic factual claims -- each claim "
        "should be a single, independently-verifiable statement. Respond with exactly "
        "one claim per line, with no numbering, bullets, or extra commentary. If the "
        "answer contains no factual claims (e.g. it is a refusal), respond with nothing.\n\n"
        f"ANSWER: {generated_answer_text}"
    )
    response_text, _, _ = cached_generate(prompt, query_id, store, model, digest)
    claims = [
        _LIST_MARKER_PATTERN.sub("", line, count=1).strip() for line in response_text.splitlines()
    ]
    return [claim for claim in claims if claim]


def claim_grounded(
    claim: str,
    retrieved_chunks: Sequence[RetrievedChunk],
    query_id: int,
    store: TraceStore,
    model: str = DEFAULT_JUDGE_MODEL_NAME,
    digest: str = DEFAULT_JUDGE_MODEL_DIGEST,
) -> bool:
    """True if the judge finds `claim` directly supported by the retrieved
    excerpts -- the atomic-claim grounding check faithfulness_score
    aggregates over every claim in an answer."""
    excerpts = "\n\n".join(f"[{i}]\n{chunk.text}" for i, chunk in enumerate(retrieved_chunks, 1))
    prompt = (
        "Given the EXCERPTS below, is the CLAIM directly supported by them? "
        "Respond with exactly one word: YES or NO.\n\n"
        f"EXCERPTS:\n{excerpts}\n\n"
        f"CLAIM: {claim}"
    )
    response_text, _, _ = cached_generate(prompt, query_id, store, model, digest)
    return response_text.strip().upper().startswith("YES")


@dataclass
class FaithfulnessResult:
    claims: list[str]
    grounded: list[bool]
    score: float


def faithfulness_score(
    generated_answer_text: str,
    retrieved_chunks: Sequence[RetrievedChunk],
    query_id: int,
    store: TraceStore,
    model: str = DEFAULT_JUDGE_MODEL_NAME,
    digest: str = DEFAULT_JUDGE_MODEL_DIGEST,
) -> FaithfulnessResult:
    """grounded claims / total claims for one generated answer. An answer
    with no extractable claims (e.g. a refusal) scores 0.0 -- there is
    nothing grounded to credit, and it should never be silently excluded
    from an aggregate mean."""
    claims = extract_claims(generated_answer_text, query_id, store, model, digest)
    if not claims:
        return FaithfulnessResult(claims=[], grounded=[], score=0.0)

    grounded = [
        claim_grounded(claim, retrieved_chunks, query_id, store, model, digest) for claim in claims
    ]
    return FaithfulnessResult(claims=claims, grounded=grounded, score=sum(grounded) / len(grounded))
