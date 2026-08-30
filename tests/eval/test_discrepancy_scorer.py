import pytest

from protocol_drift.eval.discrepancy_scorer import (
    GoldLabel,
    PredictedVerdict,
    score_discrepancy_detection,
    wilson_interval,
)

# Hand-constructed gold/predicted set, no dependency on S4-05's detector or
# S4-07's real adjudication (neither exists yet -- see the module
# docstring). One block per pair type, chosen to hit every bucket
# (TP/FP/FN/TN, ambiguous, retrieval_failed, not_applicable) at least once.

GOLD = [
    # first_posted_vs_current: TP, FP, FN, TN, ambiguous
    GoldLabel("NCT001", "first_posted_vs_current", "divergence"),
    GoldLabel("NCT002", "first_posted_vs_current", "match"),
    GoldLabel("NCT003", "first_posted_vs_current", "divergence"),
    GoldLabel("NCT004", "first_posted_vs_current", "match"),
    GoldLabel("NCT005", "first_posted_vs_current", "ambiguous"),
    # current_vs_protocol: TP, retrieval_failed, TN
    GoldLabel("NCT001", "current_vs_protocol", "divergence"),
    GoldLabel("NCT002", "current_vs_protocol", "match"),
    GoldLabel("NCT003", "current_vs_protocol", "match"),
    # registry_vs_results: not_applicable, TN
    GoldLabel("NCT001", "registry_vs_results", "divergence"),
    GoldLabel("NCT002", "registry_vs_results", "match"),
]

PREDICTIONS = [
    PredictedVerdict("NCT001", "first_posted_vs_current", "divergence"),  # TP
    PredictedVerdict("NCT002", "first_posted_vs_current", "divergence"),  # FP
    PredictedVerdict("NCT003", "first_posted_vs_current", "match"),  # FN
    PredictedVerdict("NCT004", "first_posted_vs_current", "match"),  # TN
    PredictedVerdict("NCT005", "first_posted_vs_current", "divergence"),  # ambiguous (gold side)
    PredictedVerdict("NCT001", "current_vs_protocol", "divergence"),  # TP
    PredictedVerdict("NCT002", "current_vs_protocol", None, retrieval_failed=True),
    PredictedVerdict("NCT003", "current_vs_protocol", "match"),  # TN
    # NCT001/registry_vs_results: no prediction at all -> not_applicable
    PredictedVerdict("NCT002", "registry_vs_results", "match"),  # TN
]


def test_first_posted_vs_current_confusion_counts() -> None:
    scores = score_discrepancy_detection(GOLD, PREDICTIONS)
    s = scores.per_pair["first_posted_vs_current"]
    assert (s.tp, s.fp, s.fn, s.tn) == (1, 1, 1, 1)
    assert s.ambiguous_bucket == {"gold=ambiguous,pred=divergence": 1}
    assert s.n_scored == 4


def test_first_posted_vs_current_precision_recall_f1() -> None:
    scores = score_discrepancy_detection(GOLD, PREDICTIONS)
    s = scores.per_pair["first_posted_vs_current"]
    assert s.precision == pytest.approx(0.5)
    assert s.recall == pytest.approx(0.5)
    assert s.f1 == pytest.approx(0.5)


def test_current_vs_protocol_retrieval_failure_excluded_from_confusion() -> None:
    scores = score_discrepancy_detection(GOLD, PREDICTIONS)
    s = scores.per_pair["current_vs_protocol"]
    assert (s.tp, s.fp, s.fn, s.tn) == (1, 0, 0, 1)
    assert s.retrieval_failed == 1
    assert s.precision == pytest.approx(1.0)
    assert s.recall == pytest.approx(1.0)


def test_registry_vs_results_missing_prediction_is_not_applicable_not_dropped() -> None:
    scores = score_discrepancy_detection(GOLD, PREDICTIONS)
    s = scores.per_pair["registry_vs_results"]
    assert s.not_applicable == 1
    assert (s.tp, s.fp, s.fn, s.tn) == (0, 0, 0, 1)


def test_precision_and_recall_are_none_when_denominator_is_zero() -> None:
    scores = score_discrepancy_detection(GOLD, PREDICTIONS)
    s = scores.per_pair["registry_vs_results"]
    # No positive predictions and no positive-gold-with-a-prediction here ->
    # both denominators are 0.
    assert s.precision is None
    assert s.recall is None
    assert s.f1 is None


def test_pooled_sums_across_pair_types() -> None:
    scores = score_discrepancy_detection(GOLD, PREDICTIONS)
    p = scores.pooled
    assert (p.tp, p.fp, p.fn, p.tn) == (2, 1, 1, 3)
    assert p.retrieval_failed == 1
    assert p.not_applicable == 1
    assert p.ambiguous_bucket == {"gold=ambiguous,pred=divergence": 1}
    assert p.precision == pytest.approx(2 / 3)
    assert p.recall == pytest.approx(2 / 3)
    assert p.f1 == pytest.approx(2 / 3)


def test_wilson_interval_hand_worked_8_of_10() -> None:
    # phat = 0.8, z = 1.96 (95%):
    #   denom = 1 + 1.96^2/10 = 1.38416
    #   center = 0.8 + 1.96^2/20 = 0.99204
    #   margin = 1.96 * sqrt(0.8*0.2/10 + 1.96^2/400) = 1.96 * sqrt(0.016 + 0.009604) = 0.31586...
    #   lower = (0.99204 - 0.31586) / 1.38416 = 0.49016...
    #   upper = (0.99204 + 0.31586) / 1.38416 = 0.94332...
    lower, upper = wilson_interval(8, 10)
    assert lower == pytest.approx(0.4902, abs=1e-4)
    assert upper == pytest.approx(0.9433, abs=1e-4)


def test_wilson_interval_stays_within_unit_interval_at_extremes() -> None:
    lower, upper = wilson_interval(0, 5)
    assert lower == pytest.approx(0.0)
    assert 0.0 <= upper <= 1.0

    lower, upper = wilson_interval(5, 5)
    assert upper == pytest.approx(1.0)
    assert 0.0 <= lower <= 1.0


def test_wilson_interval_zero_n_raises() -> None:
    with pytest.raises(ValueError):
        wilson_interval(0, 0)
