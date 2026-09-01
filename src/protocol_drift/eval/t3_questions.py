"""T3 question generation -- S4-06.

~60 cross-source comparison questions, one template per pairwise
comparison in `discrepancy_definition.md` SS2's table. **No gold label
here** -- S4-07's blind human adjudication supplies that, into a
deliberately separate file (`t3_gold_labels.jsonl`) so the file holding
questions and the file holding labels can never be the same file a script
could accidentally cross-contaminate.

Trial *sampling* (not labeling) uses S4-05's real detector output
(`data/discrepancy/reports/`) to stratify toward an interesting mix --
several trials the detector already flagged `divergence`, several clean
`match` trials, and a handful of `retrieval_failed` trials (so the eval
set can measure the "ambiguous != retrieval failure" distinction, not
just accuracy on easy cases). This is not circular: using the detector's
verdicts to pick *which trials are interesting to ask about* is exactly
what the spec asks for ("depends on S4-05 existing so questions can be
checked against real detector output"); the circularity the spec warns
against is using those same verdicts *as* the gold label, which this
module never does -- gold comes only from S4-07's independent read of the
actual documents.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict

from protocol_drift.eval.discrepancy_scorer import PairType, PredictedVerdict, load_detector_reports

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_REPORTS_DIR = Path("data/discrepancy/reports")
DEFAULT_OUTPUT_PATH = Path("data/eval/t3.jsonl")
DEFAULT_TARGET_QUESTION_COUNT = 60

# "Several" divergence, "several" clean-match, "a handful" retrieval-failed
# (spec step 2's own words) -- sized so 20 trials x up to 3 applicable
# templates each lands close to the ~60-question target.
N_DIVERGENCE_TRIALS = 8
N_CLEAN_MATCH_TRIALS = 8
N_RETRIEVAL_FAILED_TRIALS = 4

_TEMPLATES: dict[PairType, str] = {
    "current_vs_protocol": (
        "Does the protocol's stated primary endpoint for {nct_id} match the current "
        "registry record?"
    ),
    "first_posted_vs_current": (
        "Does the current registry record for {nct_id} match what was first posted?"
    ),
    "registry_vs_results": (
        "Do the reported results for {nct_id} match the registered primary outcome?"
    ),
}


class T3Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    nct_id: str
    pair: PairType
    question_text: str


def render_t3_question(pair: PairType, nct_id: str) -> str:
    return _TEMPLATES[pair].format(nct_id=nct_id)


# --- stratified trial sampling, from S4-05's real detector output ----------


def stratify_trials(predictions: list[PredictedVerdict]) -> dict[str, list[str]]:
    """Buckets nct_ids by what the real detector output shows -- used only
    to pick a diverse sample of trials to *ask about*, never to label an
    answer (see module docstring)."""
    by_nct: dict[str, list[PredictedVerdict]] = {}
    for p in predictions:
        by_nct.setdefault(p.nct_id, []).append(p)

    divergence = sorted(
        nct for nct, preds in by_nct.items() if any(p.verdict == "divergence" for p in preds)
    )
    retrieval_failed = sorted(
        nct for nct, preds in by_nct.items() if any(p.retrieval_failed for p in preds)
    )
    clean_match = sorted(
        nct
        for nct, preds in by_nct.items()
        if preds and all(p.verdict == "match" and not p.retrieval_failed for p in preds)
    )
    return {
        "divergence": divergence,
        "retrieval_failed": retrieval_failed,
        "clean_match": clean_match,
    }


def select_stratified_trials(
    strata: dict[str, list[str]],
    all_nct_ids: list[str],
    n_divergence: int = N_DIVERGENCE_TRIALS,
    n_clean_match: int = N_CLEAN_MATCH_TRIALS,
    n_retrieval_failed: int = N_RETRIEVAL_FAILED_TRIALS,
    seed: int = 0,
) -> list[str]:
    """Picks disjoint trials from each bucket (in divergence ->
    retrieval_failed -> clean_match order, so a trial that's both
    divergent and retrieval-failed doesn't quietly eat into both quotas),
    then rounds out to a stratified-sample-sized pool from whatever's left
    in the cohort if a bucket comes up short."""
    rng = random.Random(seed)
    picked: set[str] = set()
    selected: list[str] = []

    for bucket, n in (
        (strata.get("divergence", []), n_divergence),
        (strata.get("retrieval_failed", []), n_retrieval_failed),
        (strata.get("clean_match", []), n_clean_match),
    ):
        candidates = [nct for nct in bucket if nct not in picked]
        rng.shuffle(candidates)
        chosen = candidates[:n]
        selected.extend(chosen)
        picked.update(chosen)

    remaining_target = n_divergence + n_clean_match + n_retrieval_failed - len(selected)
    if remaining_target > 0:
        leftover = [nct for nct in all_nct_ids if nct not in picked]
        rng.shuffle(leftover)
        fill = leftover[:remaining_target]
        selected.extend(fill)
        picked.update(fill)

    return selected


# --- per-trial data availability (direct Postgres, independent of S4-05) ---


def _fetch_trial_data(
    conn: psycopg.Connection[Any], nct_ids: list[str]
) -> dict[str, dict[str, bool]]:
    """(has_first, has_current, has_results) per trial -- straight from
    Postgres `outcomes`, not from the detector's JSON reports: whether a
    pairwise question is even meaningful to *ask* about a trial (is there
    a first-posted outcome at all? a reported result at all?) is a fact
    about the registry data, independent of whether S4-05's retrieval
    happened to succeed for that trial."""
    trials = {
        nct_id: {"has_first": False, "has_current": False, "has_results": False}
        for nct_id in nct_ids
    }
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT nct_id, source FROM outcomes "
            "WHERE nct_id = ANY(%s) AND kind = 'PRIMARY'",
            (nct_ids,),
        )
        for nct_id, source in cur.fetchall():
            if nct_id not in trials:
                continue
            if source == "registered_first":
                trials[nct_id]["has_first"] = True
            elif source == "registered_current":
                trials[nct_id]["has_current"] = True
            elif source == "results_reported":
                trials[nct_id]["has_results"] = True
    return trials


# current_vs_protocol is deliberately asked even when retrieval is expected
# to fail for this trial -- per spec step 2, a retrieval-failure candidate
# is exactly the case this eval set needs, not one to filter out.
_APPLICABILITY: dict[PairType, str] = {
    "first_posted_vs_current": "has_first",
    "current_vs_protocol": "has_current",
    "registry_vs_results": "has_results",
}


def generate_t3_questions(
    nct_ids: list[str], trial_data: dict[str, dict[str, bool]]
) -> list[T3Question]:
    questions: list[T3Question] = []
    for nct_id in nct_ids:
        data = trial_data.get(nct_id)
        if data is None:
            continue
        for pair, flag_key in _APPLICABILITY.items():
            if not data[flag_key]:
                continue
            questions.append(
                T3Question(
                    question_id=f"{nct_id}:{pair}",
                    nct_id=nct_id,
                    pair=pair,
                    question_text=render_t3_question(pair, nct_id),
                )
            )
    return questions


def _stratified_cap_by_pair(
    questions: list[T3Question], target_count: int, seed: int = 0
) -> list[T3Question]:
    """Round-robins across the three pair-type buckets (shuffled with a
    fixed seed) until target_count is reached -- same downsampling
    strategy as T1's `_stratified_cap`, so the frozen set stays balanced
    across pair types rather than favoring whichever trial happened to
    generate first."""
    if len(questions) <= target_count:
        return questions

    by_pair: dict[str, list[T3Question]] = {}
    for q in questions:
        by_pair.setdefault(q.pair, []).append(q)

    rng = random.Random(seed)
    for bucket in by_pair.values():
        rng.shuffle(bucket)

    iterators = {pair: iter(bucket) for pair, bucket in by_pair.items()}
    result: list[T3Question] = []
    while len(result) < target_count and iterators:
        for pair in list(iterators):
            try:
                result.append(next(iterators[pair]))
            except StopIteration:
                del iterators[pair]
                continue
            if len(result) >= target_count:
                break
    return result


def build_t3_dataset(
    cohort: dict[str, Any],
    conn: psycopg.Connection[Any],
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    target_count: int = DEFAULT_TARGET_QUESTION_COUNT,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    seed: int = 0,
) -> dict[str, int]:
    nct_ids = [t["nct_id"] for t in cohort["trials"]]
    trial_data = _fetch_trial_data(conn, nct_ids)

    if reports_dir.exists():
        predictions = load_detector_reports(reports_dir)
    else:
        print(f"{reports_dir} not found -- S4-05 hasn't run yet, falling back to random sampling")
        predictions = []
    strata = stratify_trials(predictions)

    sampled_trials = select_stratified_trials(strata, nct_ids, seed=seed)
    all_questions = generate_t3_questions(sampled_trials, trial_data)
    frozen = _stratified_cap_by_pair(all_questions, target_count, seed=seed)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for q in frozen:
            f.write(q.model_dump_json() + "\n")

    summary = {
        "sampled_trials": len(sampled_trials),
        "candidates": len(all_questions),
        "frozen": len(frozen),
    }
    print(
        f"Sampled {summary['sampled_trials']} trial(s), generated {summary['candidates']} "
        f"candidate question(s) -> {summary['frozen']} frozen -> {output_path}"
    )
    return summary


def main() -> None:
    from protocol_drift.db import DEFAULT_DSN

    parser = argparse.ArgumentParser(description="Generate T3 cross-source comparison questions.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_QUESTION_COUNT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    cohort = json.loads(args.cohort.read_text())
    conn = psycopg.connect(args.dsn)
    build_t3_dataset(
        cohort,
        conn,
        reports_dir=args.reports_dir,
        target_count=args.target_count,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
