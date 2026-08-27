"""T1 question generation -- S3-03.

Auto-generates registry-verifiable questions from Postgres (trials/outcomes/
arms/eligibility, populated by db/extract.py from the frozen registry
archive -- never re-derived here) and locates each answer's gold chunk
ID(s) in the already-loaded `chunks` table (S3-02). The eval set is only as
good as that location step: an ungrounded gold ID is worse than a smaller
eval set, so an unlocatable candidate is logged and dropped, never forced
into the frozen set with an empty citation.

Field coverage (8 templates, per sprint_3_implementation.md): enrollment
target, primary outcome measure, primary outcome timeframe, phase, sponsor,
min age, max age, arm label. A trial's primary-outcome and arm templates can
legitimately fire more than once (some trials register several primary
outcomes or several arms) -- capped at 2 per template per trial so a
handful of data-rich trials don't dominate the frozen set.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_OUTPUT_PATH = Path("data/eval/t1.jsonl")
DEFAULT_UNLOCATABLE_LOG = Path("data/eval/t1_unlocatable.log")
DEFAULT_TARGET_COUNT = 200
MAX_PER_TEMPLATE_PER_TRIAL = 2
FUZZY_MATCH_THRESHOLD = 85.0

# clinicaltrials.gov's enum values ("PHASE2") vs. how a protocol actually
# phrases it in prose ("Phase 2") -- fixed up once here so both the stored
# gold_answer and the locate_gold_chunk search use the same human phrasing,
# rather than searching chunk text for a code no protocol would ever contain.
_PHASE_LABELS = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "N/A",
}


class T1Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    nct_id: str
    question_text: str
    gold_answer: str
    gold_chunk_ids: list[str]
    template_id: str


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


# Below this length, a fuzzy score against a whole (long) chunk is unreliable
# -- e.g. a 1-2 character numeric answer can score deceptively high against
# almost any chunk purely by chance. Short answers still get the exact
# word-boundary substring check below; they just skip the fuzzy fallback.
MIN_LENGTH_FOR_FUZZY_MATCH = 6


def _contains_as_whole_token(normalized_answer: str, normalized_text: str) -> bool:
    """Word-boundary substring check -- plain `in` would let a short numeric
    answer like "6" match inside "16" or "26" (a real bug hit while
    generating the full T1 set: an enrollment count of "6" matched 34
    chunks via naive substring search). `\\b` treats digits and letters as
    the same "word" class, so "6" won't match inside "16" but will match a
    standalone "6" or "6" followed by punctuation/whitespace."""
    return re.search(rf"\b{re.escape(normalized_answer)}\b", normalized_text) is not None


def locate_gold_chunk(
    nct_id: str,
    doc_type: str,
    answer_text: str,
    conn: psycopg.Connection[Any],
    threshold: float = FUZZY_MATCH_THRESHOLD,
) -> list[str]:
    """Every chunk_id for (nct_id, doc_type) whose text contains
    `answer_text` -- normalized, word-boundary substring first (so "PHASE2"
    style codes-turned-labels still match "Phase 2" in prose, but a short
    numeric answer can't match inside a longer number), falling back to
    rapidfuzz token_set_ratio >= `threshold` for answers long enough for that
    to be meaningful. Returns every match, not just the first -- a fact can
    legitimately appear in more than one chunk."""
    normalized_answer = _normalize(answer_text)
    if not normalized_answer:
        return []

    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_id, text FROM chunks WHERE nct_id = %s AND doc_type = %s",
            (nct_id, doc_type),
        )
        rows = cur.fetchall()

    matches = []
    for chunk_id, text in rows:
        normalized_text = _normalize(text)
        if _contains_as_whole_token(normalized_answer, normalized_text):
            matches.append(chunk_id)
        elif (
            len(normalized_answer) >= MIN_LENGTH_FOR_FUZZY_MATCH
            and fuzz.token_set_ratio(normalized_answer, normalized_text) >= threshold
        ):
            matches.append(chunk_id)
    return matches


def _locate_with_fallback(
    nct_id: str, answer_text: str, conn: psycopg.Connection[Any]
) -> list[str]:
    matches = locate_gold_chunk(nct_id, "protocol", answer_text, conn)
    if matches:
        return matches
    return locate_gold_chunk(nct_id, "sap", answer_text, conn)


# --- candidate generation: one function per template, each returning
# (question_text, gold_answer) pairs for one trial ---------------------------


def _enrollment_count(trial: dict[str, Any]) -> list[tuple[str, str]]:
    count = trial.get("enrollment_count")
    if count is None:
        return []
    return [(f"What is the target enrollment for {trial['nct_id']}?", str(count))]


def _phase(trial: dict[str, Any]) -> list[tuple[str, str]]:
    phase = trial.get("phase")
    if not phase or "|" in phase:  # multi-phase trials have no single-answer phrasing
        return []
    label = _PHASE_LABELS.get(phase)
    if not label:
        return []
    return [(f"What phase is the {trial['nct_id']} trial?", label)]


def _sponsor(trial: dict[str, Any]) -> list[tuple[str, str]]:
    sponsor = trial.get("sponsor_name")
    if not sponsor:
        return []
    return [(f"Who is the sponsor of {trial['nct_id']}?", sponsor)]


def _min_age(trial: dict[str, Any]) -> list[tuple[str, str]]:
    min_age = trial.get("min_age")
    if not min_age:
        return []
    return [(f"What is the minimum age for participants in {trial['nct_id']}?", min_age)]


def _max_age(trial: dict[str, Any]) -> list[tuple[str, str]]:
    max_age = trial.get("max_age")
    if not max_age:
        return []
    return [(f"What is the maximum age for participants in {trial['nct_id']}?", max_age)]


def _primary_outcome_measure(trial: dict[str, Any]) -> list[tuple[str, str]]:
    outcomes = trial.get("primary_outcomes", [])[:MAX_PER_TEMPLATE_PER_TRIAL]
    return [
        (f"What is primary outcome {i} for {trial['nct_id']}?", o["measure"])
        for i, o in enumerate(outcomes, start=1)
        if o.get("measure")
    ]


def _primary_outcome_timeframe(trial: dict[str, Any]) -> list[tuple[str, str]]:
    outcomes = trial.get("primary_outcomes", [])[:MAX_PER_TEMPLATE_PER_TRIAL]
    return [
        (f"What is the timeframe for primary outcome {i} of {trial['nct_id']}?", o["timeframe"])
        for i, o in enumerate(outcomes, start=1)
        if o.get("timeframe")
    ]


def _arm_label(trial: dict[str, Any]) -> list[tuple[str, str]]:
    arms = trial.get("arms", [])[:MAX_PER_TEMPLATE_PER_TRIAL]
    return [
        (f"What is the label of arm {i} in {trial['nct_id']}?", a["arm_label"])
        for i, a in enumerate(arms, start=1)
        if a.get("arm_label")
    ]


_TEMPLATES: list[tuple[str, Any]] = [
    ("enrollment_count", _enrollment_count),
    ("phase", _phase),
    ("sponsor", _sponsor),
    ("min_age", _min_age),
    ("max_age", _max_age),
    ("primary_outcome_measure", _primary_outcome_measure),
    ("primary_outcome_timeframe", _primary_outcome_timeframe),
    ("arm_label", _arm_label),
]


# --- Postgres fetch: one bulk query per table, joined in Python -------------


def _fetch_trial_data(
    conn: psycopg.Connection[Any], nct_ids: list[str]
) -> dict[str, dict[str, Any]]:
    trials: dict[str, dict[str, Any]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT nct_id, phase, sponsor_name, enrollment_count FROM trials "
            "WHERE nct_id = ANY(%s)",
            (nct_ids,),
        )
        for nct_id, phase, sponsor_name, enrollment_count in cur.fetchall():
            trials[nct_id] = {
                "nct_id": nct_id,
                "phase": phase,
                "sponsor_name": sponsor_name,
                "enrollment_count": enrollment_count,
                "primary_outcomes": [],
                "arms": [],
                "min_age": None,
                "max_age": None,
            }

        cur.execute(
            "SELECT nct_id, min_age, max_age FROM eligibility WHERE nct_id = ANY(%s)",
            (nct_ids,),
        )
        for nct_id, min_age, max_age in cur.fetchall():
            if nct_id in trials:
                trials[nct_id]["min_age"] = min_age
                trials[nct_id]["max_age"] = max_age

        cur.execute(
            "SELECT nct_id, measure, timeframe FROM outcomes "
            "WHERE nct_id = ANY(%s) AND kind = 'PRIMARY' AND source = 'registered_current' "
            "ORDER BY id",
            (nct_ids,),
        )
        for nct_id, measure, timeframe in cur.fetchall():
            if nct_id in trials:
                trials[nct_id]["primary_outcomes"].append(
                    {"measure": measure, "timeframe": timeframe}
                )

        cur.execute(
            "SELECT nct_id, arm_label FROM arms WHERE nct_id = ANY(%s) ORDER BY id",
            (nct_ids,),
        )
        for nct_id, arm_label in cur.fetchall():
            if nct_id in trials:
                trials[nct_id]["arms"].append({"arm_label": arm_label})

    return trials


def generate_t1_questions(
    cohort: dict[str, Any], conn: psycopg.Connection[Any]
) -> list[T1Question]:
    """One T1Question per (trial, template) candidate that fills to a
    non-empty answer, gold_chunk_ids already resolved via locate_gold_chunk
    (protocol first, sap fallback) -- may be an empty list for a candidate
    whose answer isn't restated anywhere in the corpus. Filtering those out
    is the caller's job (build_t1_dataset), not this function's."""
    nct_ids = [t["nct_id"] for t in cohort["trials"]]
    trial_data = _fetch_trial_data(conn, nct_ids)

    questions: list[T1Question] = []
    for nct_id in nct_ids:
        trial = trial_data.get(nct_id)
        if trial is None:
            continue
        for template_id, generator in _TEMPLATES:
            for i, (question_text, gold_answer) in enumerate(generator(trial)):
                gold_chunk_ids = _locate_with_fallback(nct_id, gold_answer, conn)
                questions.append(
                    T1Question(
                        question_id=f"{nct_id}:{template_id}:{i}",
                        nct_id=nct_id,
                        question_text=question_text,
                        gold_answer=gold_answer,
                        gold_chunk_ids=gold_chunk_ids,
                        template_id=template_id,
                    )
                )
    return questions


def _stratified_cap(
    questions: list[T1Question], target_count: int, seed: int = 0
) -> list[T1Question]:
    """Round-robins across template_id buckets (shuffled with a fixed seed
    for reproducibility) until target_count is reached, so downsampling
    doesn't quietly favor whichever template happened to generate the most
    candidates."""
    if len(questions) <= target_count:
        return questions

    by_template: dict[str, list[T1Question]] = defaultdict(list)
    for q in questions:
        by_template[q.template_id].append(q)

    rng = random.Random(seed)
    for bucket in by_template.values():
        rng.shuffle(bucket)

    iterators = {tid: iter(bucket) for tid, bucket in by_template.items()}
    result: list[T1Question] = []
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


# A gold answer matching more than this many chunks in one document is no
# longer a specific citation -- it's boilerplate (a sponsor name repeated in
# every page footer) or a short number colliding with section/table
# numbering throughout the document (confirmed while generating the full
# T1 set: an enrollment count of "13" word-boundary-matched 606 of a
# document's ~700 chunks via section numbers like "13.1", "13.2", ...).
# Treated the same as unlocatable: excluded from the frozen set and logged,
# not silently kept with a meaningless citation list.
MAX_REASONABLE_GOLD_CHUNKS = 30


def build_t1_dataset(
    cohort: dict[str, Any],
    conn: psycopg.Connection[Any],
    target_count: int = DEFAULT_TARGET_COUNT,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    unlocatable_log_path: Path = DEFAULT_UNLOCATABLE_LOG,
) -> dict[str, int]:
    all_questions = generate_t1_questions(cohort, conn)
    located = [
        q for q in all_questions if 0 < len(q.gold_chunk_ids) <= MAX_REASONABLE_GOLD_CHUNKS
    ]
    excluded = [
        q for q in all_questions if not (0 < len(q.gold_chunk_ids) <= MAX_REASONABLE_GOLD_CHUNKS)
    ]

    if excluded:
        unlocatable_log_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for q in excluded:
            reason = "unlocatable" if not q.gold_chunk_ids else "too_many_matches"
            lines.append(
                f"{q.question_id}\t{q.nct_id}\t{q.template_id}\t{reason}\t"
                f"{q.question_text}\t{q.gold_answer}"
            )
        unlocatable_log_path.write_text("\n".join(lines) + "\n")

    frozen = _stratified_cap(located, target_count)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for q in frozen:
            f.write(q.model_dump_json() + "\n")

    summary = {
        "candidates": len(all_questions),
        "located": len(located),
        "excluded": len(excluded),
        "frozen": len(frozen),
    }
    print(
        f"Generated {summary['candidates']} candidate(s), {summary['located']} located, "
        f"{summary['excluded']} excluded -> {summary['frozen']} frozen -> {output_path}"
    )
    if excluded:
        print(f"{len(excluded)} excluded candidate(s) -- see {unlocatable_log_path}")
    return summary


def main() -> None:
    from protocol_drift.db import DEFAULT_DSN

    parser = argparse.ArgumentParser(description="Generate T1 registry-verifiable questions.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_COUNT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--unlocatable-log", type=Path, default=DEFAULT_UNLOCATABLE_LOG)
    args = parser.parse_args()

    cohort = json.loads(args.cohort.read_text())
    conn = psycopg.connect(args.dsn)
    build_t1_dataset(
        cohort,
        conn,
        target_count=args.target_count,
        output_path=args.output,
        unlocatable_log_path=args.unlocatable_log,
    )


if __name__ == "__main__":
    main()
