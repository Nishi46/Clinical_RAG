import pytest

from protocol_drift.retrieval.fuse import reciprocal_rank_fusion

# Hand-worked example, k=60:
#   ranking A: [X, Y, Z]  -> ranks X=1, Y=2, Z=3
#   ranking B: [Y, X, W]  -> ranks Y=1, X=2, W=3
# scores:
#   X: 1/(60+1) + 1/(60+2) = 1/61 + 1/62
#   Y: 1/(60+2) + 1/(60+1) = 1/62 + 1/61   (same as X, by symmetry)
#   Z: 1/(60+3)            = 1/63
#   W: 1/(60+3)            = 1/63
# X and Y tie exactly (both appear at ranks {1,2} across the two lists);
# Z and W tie exactly (both appear only at rank 3, in different lists).
RANKING_A = ["X", "Y", "Z"]
RANKING_B = ["Y", "X", "W"]


def test_reciprocal_rank_fusion_hand_worked_scores() -> None:
    fused = dict(reciprocal_rank_fusion([RANKING_A, RANKING_B], k=60))

    expected_xy = 1 / 61 + 1 / 62
    expected_zw = 1 / 63

    assert fused["X"] == pytest.approx(expected_xy)
    assert fused["Y"] == pytest.approx(expected_xy)
    assert fused["Z"] == pytest.approx(expected_zw)
    assert fused["W"] == pytest.approx(expected_zw)


def test_reciprocal_rank_fusion_order_is_descending_with_correct_tie_grouping() -> None:
    fused = reciprocal_rank_fusion([RANKING_A, RANKING_B], k=60)
    order = [chunk_id for chunk_id, _ in fused]

    # X and Y (tied, highest) must both come before Z and W (tied, lowest).
    assert set(order[:2]) == {"X", "Y"}
    assert set(order[2:]) == {"Z", "W"}
    scores = [score for _, score in fused]
    assert scores == sorted(scores, reverse=True)


def test_reciprocal_rank_fusion_single_ranking_matches_plain_rrf_formula() -> None:
    fused = dict(reciprocal_rank_fusion([["A", "B", "C"]], k=60))
    assert fused["A"] == pytest.approx(1 / 61)
    assert fused["B"] == pytest.approx(1 / 62)
    assert fused["C"] == pytest.approx(1 / 63)


def test_reciprocal_rank_fusion_empty_rankings_returns_empty() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_reciprocal_rank_fusion_document_absent_from_a_ranking_only_scores_from_present_ones() -> (
    None
):
    fused = dict(reciprocal_rank_fusion([["A"], ["B"]], k=60))
    assert fused["A"] == pytest.approx(1 / 61)
    assert fused["B"] == pytest.approx(1 / 61)
