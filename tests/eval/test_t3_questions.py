from protocol_drift.eval.discrepancy_scorer import PredictedVerdict
from protocol_drift.eval.t3_questions import (
    generate_t3_questions,
    render_t3_question,
    select_stratified_trials,
    stratify_trials,
)


def test_render_t3_question_current_vs_protocol() -> None:
    text = render_t3_question("current_vs_protocol", "NCT01234567")
    assert text == (
        "Does the protocol's stated primary endpoint for NCT01234567 match the current "
        "registry record?"
    )


def test_render_t3_question_first_posted_vs_current() -> None:
    text = render_t3_question("first_posted_vs_current", "NCT01234567")
    assert text == "Does the current registry record for NCT01234567 match what was first posted?"


def test_render_t3_question_registry_vs_results() -> None:
    text = render_t3_question("registry_vs_results", "NCT01234567")
    assert text == ("Do the reported results for NCT01234567 match the registered primary outcome?")


def test_generate_t3_questions_skips_inapplicable_pairs() -> None:
    trial_data = {
        "NCT01": {"has_first": True, "has_current": True, "has_results": True},
        "NCT02": {"has_first": False, "has_current": True, "has_results": False},
    }

    questions = generate_t3_questions(["NCT01", "NCT02"], trial_data)
    by_nct: dict[str, list[str]] = {}
    for q in questions:
        by_nct.setdefault(q.nct_id, []).append(q.pair)

    assert set(by_nct["NCT01"]) == {
        "first_posted_vs_current",
        "current_vs_protocol",
        "registry_vs_results",
    }
    # NCT02 has no first-posted outcome and no reported results -- only
    # current_vs_protocol is meaningful to ask, and it's asked even though
    # nothing here says retrieval will succeed for it (that's the point).
    assert by_nct["NCT02"] == ["current_vs_protocol"]


def test_generate_t3_questions_question_id_and_text() -> None:
    trial_data = {"NCT01": {"has_first": True, "has_current": True, "has_results": True}}
    questions = generate_t3_questions(["NCT01"], trial_data)
    by_pair = {q.pair: q for q in questions}

    assert by_pair["first_posted_vs_current"].question_id == "NCT01:first_posted_vs_current"
    assert by_pair["first_posted_vs_current"].question_text == render_t3_question(
        "first_posted_vs_current", "NCT01"
    )


def test_stratify_trials_buckets_by_detector_verdicts() -> None:
    predictions = [
        PredictedVerdict("NCT01", "first_posted_vs_current", "divergence"),
        PredictedVerdict("NCT01", "current_vs_protocol", "match"),
        PredictedVerdict("NCT02", "first_posted_vs_current", "match"),
        PredictedVerdict("NCT02", "current_vs_protocol", "match"),
        PredictedVerdict("NCT03", "current_vs_protocol", None, retrieval_failed=True),
        PredictedVerdict("NCT03", "first_posted_vs_current", "match"),
    ]

    strata = stratify_trials(predictions)

    assert strata["divergence"] == ["NCT01"]
    assert strata["clean_match"] == ["NCT02"]
    assert strata["retrieval_failed"] == ["NCT03"]


def test_select_stratified_trials_picks_disjoint_sets() -> None:
    strata = {
        "divergence": ["NCT01", "NCT02"],
        "retrieval_failed": ["NCT02", "NCT03"],  # NCT02 overlaps divergence
        "clean_match": ["NCT04", "NCT05"],
    }

    selected = select_stratified_trials(
        strata,
        all_nct_ids=["NCT01", "NCT02", "NCT03", "NCT04", "NCT05", "NCT06"],
        n_divergence=2,
        n_retrieval_failed=2,
        n_clean_match=2,
    )

    # No duplicates even though NCT02 appears in two buckets -- the
    # retrieval_failed bucket has only one trial left (NCT03) once NCT02 is
    # excluded as already-picked, so the shortfall backfills one more trial
    # (NCT06) from the rest of the cohort to still reach the 6-trial target.
    assert len(selected) == len(set(selected))
    assert len(selected) == 6
    assert {"NCT01", "NCT02", "NCT03", "NCT04", "NCT05"} <= set(selected)


def test_select_stratified_trials_fills_remainder_when_bucket_short() -> None:
    strata = {"divergence": ["NCT01"], "retrieval_failed": [], "clean_match": []}

    selected = select_stratified_trials(
        strata,
        all_nct_ids=["NCT01", "NCT02", "NCT03"],
        n_divergence=1,
        n_retrieval_failed=1,
        n_clean_match=1,
    )

    # Empty retrieval_failed/clean_match buckets get backfilled from the
    # rest of the cohort rather than silently shrinking the sample.
    assert len(selected) == 3
