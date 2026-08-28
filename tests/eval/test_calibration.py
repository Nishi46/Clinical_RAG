import pytest

from protocol_drift.eval.calibration import cohens_kappa, confusion_matrix

# Hand-worked example (see the derivation in the S3-08 implementation
# notes): 4 pairs, one disagreement (human=1, judge=0).
#   confusion matrix (rows=human idx, cols=judge idx, order 0/0.5/1):
#     [[1, 0, 0],
#      [0, 1, 0],
#      [1, 0, 1]]
#   observed weighted disagreement = 1/4 = 0.25
#   expected weighted disagreement = 8/16 = 0.5
#   kappa = 1 - 0.25/0.5 = 0.5
HUMAN = [0.0, 0.5, 1.0, 1.0]
JUDGE = [0.0, 0.5, 1.0, 0.0]


def test_confusion_matrix_hand_worked_example() -> None:
    assert confusion_matrix(HUMAN, JUDGE) == [
        [1, 0, 0],
        [0, 1, 0],
        [1, 0, 1],
    ]


def test_cohens_kappa_hand_worked_example() -> None:
    assert cohens_kappa(HUMAN, JUDGE) == pytest.approx(0.5)


def test_cohens_kappa_perfect_agreement_is_one() -> None:
    labels = [0.0, 0.5, 1.0, 1.0, 0.0, 0.5]
    assert cohens_kappa(labels, labels) == pytest.approx(1.0)


def test_cohens_kappa_snaps_unrounded_scores_to_fixed_labels() -> None:
    # Defensive rounding: a near-0.5 float should behave identically to an
    # exact 0.5 once snapped to the fixed {0, 0.5, 1} label set.
    assert cohens_kappa([0.51, 0.0, 1.0], [0.49, 0.0, 1.0]) == pytest.approx(1.0)


def test_cohens_kappa_mismatched_lengths_raises() -> None:
    with pytest.raises(ValueError):
        cohens_kappa([0.0, 1.0], [0.0])


def test_cohens_kappa_empty_raises() -> None:
    with pytest.raises(ValueError):
        cohens_kappa([], [])


def test_cohens_kappa_max_disagreement_is_negative() -> None:
    # Every pair disagrees maximally (0 vs 1), worse than chance -- kappa
    # should be negative, not just "low".
    human = [0.0, 0.0, 1.0, 1.0]
    judge = [1.0, 1.0, 0.0, 0.0]
    assert cohens_kappa(human, judge) < 0
