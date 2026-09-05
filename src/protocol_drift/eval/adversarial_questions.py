"""Adversarial question set + refusal metrics -- S4-09 (cuttable per
`sprint_4_implementation.md`'s cut list, but S4-01 through S4-08 all
landed, so this gets built too).

Two genuinely-unanswerable-from-the-corpus categories, per
`project_plan.md` SS7.1's own framing ("questions about unposted
documents, or facts no protocol contains"):

- **unposted_document**: a SAP-specific question for a trial with no SAP
  text actually retrievable in the corpus. This uses real chunk presence
  (`chunks` where `doc_type='sap'`), not the registry's own `has_sap`
  flag -- the flag turns out to be a poor predictor here: only 1 of 200
  cohort trials has `has_sap=False`, but a one-off count against the
  loaded corpus found 129 of the 199 `has_sap=True` trials have *zero*
  SAP chunks actually ingested (a real corpus-thinness gap, the kind
  `corpus_assessment.md` SS4 already flags for protocol documents). "The
  registry says a SAP was posted" and "the text is actually retrievable
  from this corpus" are different facts, and the adversarial set needs
  the second one -- a question genuinely can't be answered from a
  document this system never got real text for, regardless of what the
  registry claims.
- **fact_not_in_corpus**: a question about something no clinical protocol
  or SAP would ever document (personal contact info, supply-chain
  trivia, unrelated administrative minutiae) -- categorically absent
  regardless of which trial it's asked about, so no per-trial retrieval
  check is needed for this category.

`refusal_metrics` runs a caller-supplied `generate_fn` over the
adversarial set (refusal accuracy) and over a sample of already-answerable
T1/T2 questions (over-refusal rate), checking both against the exact same
`is_refusal` (generation/answer.py) `generate_answer` itself uses -- a
hedge like "I'm not sure, but possibly X" is not a clean refusal.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, ConfigDict

from protocol_drift.eval.models import EvalQuestion
from protocol_drift.generation.answer import is_refusal

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_OUTPUT_PATH = Path("data/eval/adversarial.jsonl")
DEFAULT_TARGET_COUNT = 30

Category = Literal["unposted_document", "fact_not_in_corpus"]

_UNPOSTED_DOCUMENT_TEMPLATES: tuple[str, ...] = (
    "What does the statistical analysis plan specify for handling missing data in {nct_id}?",
    "What multiplicity adjustment method does the statistical analysis plan use for {nct_id}?",
    "According to the statistical analysis plan for {nct_id}, what is the primary analysis "
    "population (ITT, per-protocol, or other)?",
    "What sensitivity analyses does the statistical analysis plan specify for {nct_id}?",
    "What is the pre-specified Type I error allocation across endpoints in the statistical "
    "analysis plan for {nct_id}?",
)

# Categorically absent from any clinical protocol or SAP -- personal
# contact information, supply-chain trivia, and administrative minutiae
# that these documents never record, regardless of trial.
_FACT_NOT_IN_CORPUS_TEMPLATES: tuple[str, ...] = (
    "What is the manufacturing lot number of the study drug used in {nct_id}?",
    "What brand of laptop computer did site staff use to enter data for {nct_id}?",
    "What is the personal cell phone number of the principal investigator for {nct_id}?",
    "What is the color of the packaging box used to ship the study drug for {nct_id}?",
    "How many parking spaces are available at the primary study site for {nct_id}?",
    "What is the home address of the study's biostatistician for {nct_id}?",
    "What software version of Microsoft Excel was used to create the case report forms for "
    "{nct_id}?",
    "What was the weather like on the day the first patient was enrolled in {nct_id}?",
)


class AdversarialQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    nct_id: str
    question_text: str
    category: Category
    expected_behavior: Literal["refuse"] = "refuse"


def _trials_with_no_sap_text(conn: psycopg.Connection[Any], nct_ids: list[str]) -> list[str]:
    """Trials with zero actual `doc_type='sap'` chunks in the corpus --
    real retrievability, not the `trials.has_sap` self-reported flag (see
    module docstring for why those two disagree substantially here)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT nct_id FROM trials WHERE nct_id = ANY(%s) AND NOT EXISTS "
            "(SELECT 1 FROM chunks c WHERE c.nct_id = trials.nct_id AND c.doc_type = 'sap')",
            (nct_ids,),
        )
        return [row[0] for row in cur.fetchall()]


def generate_adversarial_questions(
    conn: psycopg.Connection[Any],
    nct_ids: list[str],
    target_count: int = DEFAULT_TARGET_COUNT,
    seed: int = 0,
) -> list[AdversarialQuestion]:
    rng = random.Random(seed)
    n_unposted = target_count // 2
    n_fact = target_count - n_unposted

    no_sap_trials = _trials_with_no_sap_text(conn, nct_ids)
    rng.shuffle(no_sap_trials)

    unposted: list[AdversarialQuestion] = []
    for i, nct_id in enumerate(no_sap_trials[:n_unposted]):
        template = _UNPOSTED_DOCUMENT_TEMPLATES[i % len(_UNPOSTED_DOCUMENT_TEMPLATES)]
        unposted.append(
            AdversarialQuestion(
                question_id=f"{nct_id}:unposted_document:{i}",
                nct_id=nct_id,
                question_text=template.format(nct_id=nct_id),
                category="unposted_document",
            )
        )

    fact_trials = list(nct_ids)
    rng.shuffle(fact_trials)
    facts: list[AdversarialQuestion] = []
    for i in range(min(n_fact, len(fact_trials))):
        nct_id = fact_trials[i]
        template = _FACT_NOT_IN_CORPUS_TEMPLATES[i % len(_FACT_NOT_IN_CORPUS_TEMPLATES)]
        facts.append(
            AdversarialQuestion(
                question_id=f"{nct_id}:fact_not_in_corpus:{i}",
                nct_id=nct_id,
                question_text=template.format(nct_id=nct_id),
                category="fact_not_in_corpus",
            )
        )

    return unposted + facts


def build_adversarial_dataset(
    cohort: dict[str, Any],
    conn: psycopg.Connection[Any],
    target_count: int = DEFAULT_TARGET_COUNT,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    seed: int = 0,
) -> dict[str, int]:
    nct_ids = [t["nct_id"] for t in cohort["trials"]]
    questions = generate_adversarial_questions(conn, nct_ids, target_count=target_count, seed=seed)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for q in questions:
            f.write(q.model_dump_json() + "\n")

    from collections import Counter

    by_category = Counter(q.category for q in questions)
    print(f"Wrote {len(questions)} adversarial question(s) -> {output_path}")
    print(f"  by category: {dict(by_category)}")
    return {"total": len(questions), **{k: v for k, v in by_category.items()}}


# --- refusal metrics ---------------------------------------------------


@dataclass
class RefusalScores:
    n_adversarial: int
    refusal_accuracy: float  # fraction of adversarial questions correctly refused
    n_answerable_sample: int
    over_refusal_rate: float  # fraction of answerable questions incorrectly refused


def refusal_metrics(
    adversarial_questions: Sequence[AdversarialQuestion],
    answerable_questions: Sequence[EvalQuestion],
    generate_fn: Callable[[str], str],
) -> RefusalScores:
    """`generate_fn(question_text) -> response_text` -- deliberately just a
    string in, string out contract (not tied to `EvalQuestion`/retrieval
    specifics) so a caller can plug in the real retrieval+generation
    pipeline or a canned stub for testing without this function caring
    which."""
    n_adversarial = len(adversarial_questions)
    refused = sum(1 for q in adversarial_questions if is_refusal(generate_fn(q.question_text)))

    n_answerable = len(answerable_questions)
    over_refused = sum(1 for q in answerable_questions if is_refusal(generate_fn(q.question_text)))

    return RefusalScores(
        n_adversarial=n_adversarial,
        refusal_accuracy=refused / n_adversarial if n_adversarial else 0.0,
        n_answerable_sample=n_answerable,
        over_refusal_rate=over_refused / n_answerable if n_answerable else 0.0,
    )


def render_refusal_eval_md(scores: RefusalScores) -> str:
    lines = ["# Refusal evaluation (S4-09)", ""]
    lines.append(
        f"**Refusal accuracy: {scores.refusal_accuracy:.1%}** (n={scores.n_adversarial} "
        "adversarial questions genuinely unanswerable from the corpus -- correctly says "
        "`NOT_ANSWERABLE`)."
    )
    lines.append("")
    lines.append(
        f"**Over-refusal rate: {scores.over_refusal_rate:.1%}** (n={scores.n_answerable_sample} "
        "already-answerable T1/T2 questions -- incorrectly refuses when the answer is actually "
        "retrievable)."
    )
    lines.append("")
    lines.append(
        'A hedge ("I\'m not sure, but possibly X") is never counted as a refusal on either '
        "side -- only an exact `NOT_ANSWERABLE` token counts, the same check `generate_answer` "
        "itself uses."
    )
    lines.append("")
    return "\n".join(lines)


def _load_eval_questions(path: Path) -> list[EvalQuestion]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [
        EvalQuestion(
            question_id=row["question_id"],
            nct_id=row["nct_id"],
            question_text=row["question_text"],
            gold_answer=row["gold_answer"],
            gold_chunk_ids=row["gold_chunk_ids"],
            template_id=row.get("template_id"),
            gold_answer_notes=row.get("gold_answer_notes"),
        )
        for row in rows
    ]


def main() -> None:
    from protocol_drift.db import DEFAULT_DSN

    parser = argparse.ArgumentParser(description="Generate the S4-09 adversarial question set.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_COUNT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    cohort = json.loads(args.cohort.read_text())
    conn = psycopg.connect(args.dsn)
    build_adversarial_dataset(cohort, conn, target_count=args.target_count, output_path=args.output)


if __name__ == "__main__":
    main()
