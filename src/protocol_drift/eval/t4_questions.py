"""T4 question generation -- S4-02.

Amendment-aware questions over the registry's own revision history --
"was the primary outcome changed, when, and to what," "how many revisions
touched it," "what did it say as first registered." Gold answers come
straight from Postgres (`amendments` + `outcomes`, both S1-05, already
loaded for the full cohort) -- no chunk-location step, unlike T1, since
these are registry-only facts (`gold_chunk_ids` is always `[]`).

**Scope note on "when, and to what":** full per-revision diff text (S4-01
steps 3-4: fetching each intermediate version snapshot and diffing
`measure`/`timeFrame`/`description` at that specific revision) isn't built
yet -- only `registered_first` (version 0) and `registered_current` are
structured in Postgres (see `db/schema.sql`'s comment on `outcomes.version`).
For a trial with exactly **one** outcome-touching revision, that's not a
limitation: `registered_first` is necessarily the text *before* that one
revision and `registered_current` the text *after* it, so "from X to Y at
version N" is fully grounded, real data (55 of 197 outcome-touching cohort
trials, per a one-off count against the loaded DB). For a trial with
multiple outcome-touching revisions, attributing a specific before/after
pair to one specific revision would require the intermediate snapshots
this module doesn't fetch -- so the multi-revision answer states the
count and the most recent revision's version/date plus the current text,
and does not claim to know what the text read at any specific earlier
revision. Both phrasings are literally true of what's in Postgres today;
neither fabricates data S4-01 hasn't extracted.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict

from protocol_drift.discrepancy.amendments import OUTCOME_MODULE_LABEL, AmendmentEvent

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_OUTPUT_PATH = Path("data/eval/t4.jsonl")
DEFAULT_TARGET_COUNT = 40


class T4Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    nct_id: str
    question_text: str
    gold_answer: str
    gold_chunk_ids: list[str]
    template_id: str


# --- candidate generation: one function per template, each returning
# (question_text, gold_answer) pairs for one trial ---------------------------


def _changed_when_what(trial: dict[str, Any]) -> list[tuple[str, str]]:
    current = trial["current_measure"]
    if current is None:
        return []
    nct_id = trial["nct_id"]
    events: list[AmendmentEvent] = trial["events"]
    question = (
        f"Was the primary outcome changed after first posting for {nct_id}? When, and to what?"
    )

    if not events:
        return [(question, "No, the primary outcome was not changed after first posting.")]

    if len(events) == 1:
        first = trial["first_measure"]
        if first is None:
            return []
        event = events[0]
        answer = (
            f"Yes, the primary outcome was changed, from '{first}' to '{current}', "
            f"at version {event.version} ({event.date})."
        )
        return [(question, answer)]

    latest = events[-1]
    answer = (
        f"Yes, the primary outcome was changed across {len(events)} revisions "
        f"(most recently at version {latest.version}, {latest.date}). It now reads: '{current}'."
    )
    return [(question, answer)]


def _revision_count(trial: dict[str, Any]) -> list[tuple[str, str]]:
    nct_id = trial["nct_id"]
    events: list[AmendmentEvent] = trial["events"]
    return [(f"How many revisions touched the primary outcome for {nct_id}?", str(len(events)))]


def _first_registered(trial: dict[str, Any]) -> list[tuple[str, str]]:
    first = trial["first_measure"]
    if first is None:
        return []
    nct_id = trial["nct_id"]
    return [(f"What was the primary outcome as first registered for {nct_id}?", first)]


_TEMPLATES: list[tuple[str, Callable[[dict[str, Any]], list[tuple[str, str]]]]] = [
    ("changed_when_what", _changed_when_what),
    ("revision_count", _revision_count),
    ("first_registered", _first_registered),
]


# --- Postgres fetch: bulk, one query per table, joined in Python ------------


def _fetch_trial_data(
    conn: psycopg.Connection[Any], nct_ids: list[str]
) -> dict[str, dict[str, Any]]:
    trials: dict[str, dict[str, Any]] = {
        nct_id: {"nct_id": nct_id, "first_measure": None, "current_measure": None, "events": []}
        for nct_id in nct_ids
    }

    with conn.cursor() as cur:
        cur.execute(
            "SELECT nct_id, version, date, modules_changed FROM amendments "
            "WHERE nct_id = ANY(%s) AND %s = ANY(modules_changed) ORDER BY nct_id, version",
            (nct_ids, OUTCOME_MODULE_LABEL),
        )
        for nct_id, version, date, modules_changed in cur.fetchall():
            if nct_id in trials:
                trials[nct_id]["events"].append(
                    AmendmentEvent(
                        nct_id=nct_id,
                        version=version,
                        date=date,
                        modules_changed=list(modules_changed),
                    )
                )

        # DISTINCT ON (nct_id) ... ORDER BY nct_id, id: a trial can register
        # more than one primary outcome (e.g. the CheckMate-649 example, per
        # discrepancy_definition.md SS3); T4's templates ask about "the"
        # primary outcome singular, so this takes the first-inserted row per
        # trial, same tie-break T1 uses when it caps per-template output.
        cur.execute(
            "SELECT DISTINCT ON (nct_id) nct_id, measure FROM outcomes "
            "WHERE nct_id = ANY(%s) AND kind = 'PRIMARY' AND source = 'registered_first' "
            "ORDER BY nct_id, id",
            (nct_ids,),
        )
        for nct_id, measure in cur.fetchall():
            if nct_id in trials:
                trials[nct_id]["first_measure"] = measure

        cur.execute(
            "SELECT DISTINCT ON (nct_id) nct_id, measure FROM outcomes "
            "WHERE nct_id = ANY(%s) AND kind = 'PRIMARY' AND source = 'registered_current' "
            "ORDER BY nct_id, id",
            (nct_ids,),
        )
        for nct_id, measure in cur.fetchall():
            if nct_id in trials:
                trials[nct_id]["current_measure"] = measure

    return trials


def generate_t4_questions(
    cohort: dict[str, Any], conn: psycopg.Connection[Any]
) -> list[T4Question]:
    """One T4Question per (trial, template) candidate that fills to a
    non-empty answer. `gold_chunk_ids` is always `[]` -- these are
    registry-only facts (spec item 2), no retrieval/location step."""
    nct_ids = [t["nct_id"] for t in cohort["trials"]]
    trial_data = _fetch_trial_data(conn, nct_ids)

    questions: list[T4Question] = []
    for nct_id in nct_ids:
        trial = trial_data.get(nct_id)
        if trial is None:
            continue
        for template_id, generator in _TEMPLATES:
            for i, (question_text, gold_answer) in enumerate(generator(trial)):
                questions.append(
                    T4Question(
                        question_id=f"{nct_id}:{template_id}:{i}",
                        nct_id=nct_id,
                        question_text=question_text,
                        gold_answer=gold_answer,
                        gold_chunk_ids=[],
                        template_id=template_id,
                    )
                )
    return questions


def _stratified_cap(
    questions: list[T4Question], target_count: int, seed: int = 0
) -> list[T4Question]:
    """Round-robins across template_id buckets (shuffled with a fixed seed
    for reproducibility) until target_count is reached -- same downsampling
    strategy as T1's `_stratified_cap`, so no single template dominates the
    frozen set."""
    if len(questions) <= target_count:
        return questions

    by_template: dict[str, list[T4Question]] = {}
    for q in questions:
        by_template.setdefault(q.template_id, []).append(q)

    rng = random.Random(seed)
    for bucket in by_template.values():
        rng.shuffle(bucket)

    iterators = {tid: iter(bucket) for tid, bucket in by_template.items()}
    result: list[T4Question] = []
    while len(result) < target_count and iterators:
        for tid in list(iterators):
            try:
                result.append(next(iterators[tid]))
            except StopIteration:
                del iterators[tid]
                continue
            if len(result) >= target_count:
                break
    return result


def build_t4_dataset(
    cohort: dict[str, Any],
    conn: psycopg.Connection[Any],
    target_count: int = DEFAULT_TARGET_COUNT,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    seed: int = 0,
) -> dict[str, int]:
    """Every candidate question from an unchanged trial (zero
    outcome-touching revisions) is kept unconditionally -- per spec item 3,
    a system that only ever answers "yes it changed" must be caught here,
    not first noticed in S4-08's false-positive count, and there are only a
    handful of such trials in the cohort to begin with (3 of 200, as of the
    loaded archive), so nothing is lost by keeping all of them. The rest of
    `target_count` is filled by stratified sampling over the
    outcome-touching trials' candidates."""
    all_questions = generate_t4_questions(cohort, conn)

    unchanged_nct_ids = {
        q.nct_id
        for q in all_questions
        if q.template_id == "revision_count" and q.gold_answer == "0"
    }

    unchanged_questions = [q for q in all_questions if q.nct_id in unchanged_nct_ids]
    changed_questions = [q for q in all_questions if q.nct_id not in unchanged_nct_ids]

    remaining = max(target_count - len(unchanged_questions), 0)
    frozen = unchanged_questions + _stratified_cap(changed_questions, remaining, seed=seed)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for q in frozen:
            f.write(q.model_dump_json() + "\n")

    summary = {
        "candidates": len(all_questions),
        "unchanged_trial_questions": len(unchanged_questions),
        "frozen": len(frozen),
    }
    print(
        f"Generated {summary['candidates']} candidate(s), kept "
        f"{summary['unchanged_trial_questions']} unchanged-trial question(s) unconditionally "
        f"-> {summary['frozen']} frozen -> {output_path}"
    )
    return summary


def main() -> None:
    from protocol_drift.db import DEFAULT_DSN

    parser = argparse.ArgumentParser(description="Generate T4 amendment-aware questions.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_COUNT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    cohort = json.loads(args.cohort.read_text())
    conn = psycopg.connect(args.dsn)
    build_t4_dataset(cohort, conn, target_count=args.target_count, output_path=args.output)


if __name__ == "__main__":
    main()
