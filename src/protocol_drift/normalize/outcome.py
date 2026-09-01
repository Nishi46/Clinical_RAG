"""Outcome normalization layer -- S4-03. The hard one and the interesting
one (`sprint_plan.md`'s own framing): per `discrepancy_definition.md` SS1,
a naive text-diff between two outcome fields false-positives roughly
4-in-5 times (31.7% raw registry-vs-registry change rate vs. ~8% of that
literature's manually-reviewed *true* change rate). This module is what
stands between a credible detector and a noisy one -- built and tested as
a standalone unit, never inlined into S4-05.

`compare_outcomes` is the entry point S4-05 will call. It takes two raw
outcome-measure text blocks -- each caller's choice of composite (in
practice, this cohort's registry `measure` text is already a full
descriptive sentence, not a bare construct label; see the NCT02872116
fixture in the tests) -- and returns a match/divergence/ambiguous verdict
per `discrepancy_definition.md` SS3's rubric, reused verbatim in the judge
prompt (`_RUBRIC` below) rather than re-derived.

A timeframe-only difference that normalizes to the same canonical value
(SS1's stated dominant false-positive source) is resolved deterministically
in code: zero judge calls, zero cost, always correct given a parseable
duration. Everything else goes to the judge -- the same local model S3-08
calibrated, called through the same `cached_generate`/`TraceStore` plumbing
`correctness_scorer.py` already established; no new "judge client"
abstraction.

`NormalizedOutcome`/`normalize_outcome` expose the canonical form of *one*
side, built from registry data that's already three separate Postgres
columns (`outcomes.measure`/`timeframe`/`description`, S1-05) rather than
one blob to split. `compare_outcomes` doesn't need it -- the judge
classifies a raw text pair holistically, per the rubric, rather than
comparing separately-extracted fields -- but a caller that wants the
normalized fields on their own (e.g. a report citation) can build one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from protocol_drift.generation.answer import cached_generate
from protocol_drift.normalize.text import (
    extract_durations_in_months,
    normalize_text,
    strip_durations,
)
from protocol_drift.trace.store import TraceStore

logger = logging.getLogger(__name__)

Verdict = Literal["match", "divergence", "ambiguous"]

# Mirrors configs/models.yaml's `judge` entry -- same literal-constant
# convention as eval/correctness_scorer.py's DEFAULT_JUDGE_MODEL_NAME/DIGEST.
DEFAULT_JUDGE_MODEL_NAME = "llama3.1:latest"
DEFAULT_JUDGE_MODEL_DIGEST = (
    "sha256:46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e"
)


@dataclass
class NormalizedOutcome:
    construct: str
    population: str
    timeframe: float | None
    raw_text: str


def normalize_outcome(
    measure: str, timeframe_text: str | None = None, description: str | None = None
) -> NormalizedOutcome:
    """Builds the canonical form of one side's registry data.
    `timeframe_text` falls back to `measure` when absent, since some
    outcome text embeds its duration inline (e.g. "...at 24 months")
    rather than in a separate timeframe field."""
    duration_source = timeframe_text if timeframe_text else measure
    return NormalizedOutcome(
        construct=normalize_text(measure),
        population=normalize_text(description) if description else "",
        timeframe=normalize_timeframe(duration_source),
        raw_text=measure,
    )


def normalize_timeframe(text: str) -> float | None:
    """Canonical single duration, in months, or `None` if `text` contains
    zero or more-than-one *distinct* duration mention. Reuses S3-07's
    `extract_durations_in_months` (same regex + unit-conversion table,
    per `normalize/text.py`'s own stated purpose of not growing a second
    copy of this) rather than re-deriving it -- a bare wrapper narrowing
    "every duration mentioned" down to "the one canonical value," when
    there is exactly one to narrow to. Multiple distinct durations in one
    field (e.g. a compound timeframe description) is genuinely ambiguous,
    not this function's call to resolve."""
    durations = extract_durations_in_months(text)
    if len(durations) == 1:
        return next(iter(durations))
    return None


# Reused verbatim from discrepancy_definition.md SS3's match/divergence/
# ambiguous table -- the substantive classification criteria only; the
# table's own meta-commentary (footnotes referencing "S4-03" and "SS1" by
# name) is dropped since it's about the doc itself, not about how to
# classify two outcome texts, and would only confuse the model.
_RUBRIC = (
    "MATCH: Same clinical construct, same population, same timeframe (or a timeframe "
    'difference attributable only to unit/format, e.g. "24 months" vs. "2 years") -- '
    "semantically equivalent phrasing is a match, not a divergence.\n"
    "DIVERGENCE: Different clinical construct (e.g., overall survival -> progression-free "
    "survival), different comparator arm, a materially different population, or a different "
    "primary-vs-secondary designation for the same measure.\n"
    "AMBIGUOUS: Wording changed enough that automatic normalization can't confidently "
    "classify it either way, multiple primary outcomes present in one source and not the "
    "other (partial overlap), or the protocol section couldn't be reliably retrieved "
    "(retrieval failure, not evidence of divergence -- do not default retrieval failure to "
    "divergence)."
)

_VERDICT_PATTERN = re.compile(r"VERDICT:\s*(match|divergence|ambiguous)", re.IGNORECASE)


def _construct_prompt(a: str, b: str) -> str:
    return (
        "You are comparing two clinical-trial primary-outcome text blocks -- OUTCOME A (one "
        "source's stated primary outcome) and OUTCOME B (another source's) -- to determine "
        "whether they describe the same measurement. Classify per this rubric:\n\n"
        f"{_RUBRIC}\n\n"
        f"OUTCOME A: {a}\n\n"
        f"OUTCOME B: {b}\n\n"
        "Respond in exactly this format:\n"
        "VERDICT: <match, divergence, or ambiguous>\n"
        "JUSTIFICATION: <one sentence>"
    )


def _parse_verdict(response_text: str) -> Verdict | None:
    match = _VERDICT_PATTERN.search(response_text)
    if match is None:
        return None
    verdict = match.group(1).lower()
    if verdict not in ("match", "divergence", "ambiguous"):
        return None
    return verdict  # type: ignore[return-value]


def normalize_construct(
    a: str,
    b: str,
    query_id: int,
    store: TraceStore,
    model: str = DEFAULT_JUDGE_MODEL_NAME,
    digest: str = DEFAULT_JUDGE_MODEL_DIGEST,
) -> tuple[Verdict, str]:
    """The judge call for everything a regex/keyword pass can't cover --
    arbitrary phrasing of the same (or a different) clinical construct and
    population. Parses defensively, one retry on an unparseable response
    (same pattern as `correctness_scorer.judged_correctness`), and falls
    back to "ambiguous" -- never "match" (would hide a normalizer
    failure) and never "divergence" (`discrepancy_definition.md`'s ethics
    stance: an unresolved case is a candidate for human review, not an
    accusation) -- if the retry is unparseable too. Returns
    (verdict, judge_response_text) so the justification is always
    available for `docs/normalization.md`."""
    prompt = _construct_prompt(a, b)
    response_text, _, _ = cached_generate(prompt, query_id, store, model, digest)
    verdict = _parse_verdict(response_text)

    if verdict is None:
        retry_prompt = (
            prompt + "\n\n(Your previous response didn't match the required format. "
            "Please respond in exactly the format requested.)"
        )
        response_text, _, _ = cached_generate(retry_prompt, query_id, store, model, digest)
        verdict = _parse_verdict(response_text)

    if verdict is None:
        logger.warning("normalize_construct: unparseable judge response: %r", response_text)
        verdict = "ambiguous"
    return verdict, response_text


@dataclass
class NormalizationResult:
    verdict: Verdict
    method: Literal["identical_text", "timeframe_deterministic", "judge"]
    judge_response: str | None = None


def compare_outcomes(
    a: str,
    b: str,
    query_id: int,
    store: TraceStore,
    model: str = DEFAULT_JUDGE_MODEL_NAME,
    digest: str = DEFAULT_JUDGE_MODEL_DIGEST,
) -> NormalizationResult:
    """The full comparison pipeline: two deterministic, zero-judge-call
    shortcuts, then the judge for everything else.

    1. Identical text (after `normalize_text`) -- trivially a match.
    2. A pure timeframe rewording: strip each side's duration mention,
       normalize what's left, and compare. If the remainder is identical
       AND both sides' durations normalize to the *same* canonical months
       value, this is SS1's literature-dominant false-positive source and a
       match, no judge call needed. Two genuinely *different* duration
       values are NOT covered by this shortcut (that's a real timeframe
       change, not a unit rewording, and the rubric gives it no obvious
       deterministic classification) -- it falls through to the judge like
       anything else.
    3. Otherwise: `normalize_construct`'s judge call.
    """
    if normalize_text(a) == normalize_text(b):
        return NormalizationResult(verdict="match", method="identical_text")

    stripped_a = normalize_text(strip_durations(a))
    stripped_b = normalize_text(strip_durations(b))
    time_a = normalize_timeframe(a)
    time_b = normalize_timeframe(b)
    if (
        stripped_a == stripped_b
        and time_a is not None
        and time_b is not None
        and time_a == time_b
    ):
        return NormalizationResult(verdict="match", method="timeframe_deterministic")

    verdict, judge_response = normalize_construct(a, b, query_id, store, model, digest)
    return NormalizationResult(verdict=verdict, method="judge", judge_response=judge_response)


# --- S4-03 step 6: evaluation against the hand-labeled phrase-pair set ------

LABELS: tuple[Verdict, ...] = ("match", "divergence", "ambiguous")


@dataclass(frozen=True)
class PhrasePair:
    pair_id: str
    outcome_a: str
    outcome_b: str
    label: Verdict
    category: str
    note: str = ""


@dataclass
class NormalizationReport:
    n: int
    accuracy: float
    confusion: dict[Verdict, dict[Verdict, int]]  # confusion[gold][predicted]
    method_counts: dict[str, int]
    predictions: list[tuple[str, Verdict, Verdict, str]]  # (pair_id, gold, predicted, method)


def evaluate_normalization(
    phrase_pairs: list[PhrasePair],
    store: TraceStore,
    model: str = DEFAULT_JUDGE_MODEL_NAME,
    digest: str = DEFAULT_JUDGE_MODEL_DIGEST,
) -> NormalizationReport:
    """Runs `compare_outcomes` over every hand-labeled pair and reports
    accuracy + a full 3x3 confusion matrix against the hand-assigned
    label -- kept separate from any downstream discrepancy P/R/F1 (S4-08),
    per `sprint_plan.md`'s framing that normalization quality is its own
    reportable thing, not folded into the end-to-end detector metric."""
    confusion: dict[Verdict, dict[Verdict, int]] = {g: dict.fromkeys(LABELS, 0) for g in LABELS}
    method_counts: dict[str, int] = {}
    predictions: list[tuple[str, Verdict, Verdict, str]] = []
    correct = 0

    for pair in phrase_pairs:
        query_id = store.log_query(f"normalization_eval:{pair.pair_id}")
        result = compare_outcomes(pair.outcome_a, pair.outcome_b, query_id, store, model, digest)
        confusion[pair.label][result.verdict] += 1
        method_counts[result.method] = method_counts.get(result.method, 0) + 1
        predictions.append((pair.pair_id, pair.label, result.verdict, result.method))
        if result.verdict == pair.label:
            correct += 1

    n = len(phrase_pairs)
    return NormalizationReport(
        n=n,
        accuracy=correct / n if n else 0.0,
        confusion=confusion,
        method_counts=method_counts,
        predictions=predictions,
    )
