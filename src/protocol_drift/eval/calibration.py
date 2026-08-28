"""Judge calibration -- S3-08.

Weighted Cohen's kappa between human and judge correctness labels. Linear
weights, not unweighted agreement, because the 0/0.5/1 scale is ordinal --
a judge that says 0 when the human says 1 is a worse disagreement than a
judge that says 0.5 when the human says 1, and unweighted kappa treats both
the same. Implemented directly (no scikit-learn) -- the formula is a
handful of lines and this project's "Plain Python" stack avoids a new
dependency for one function, the same call S3-05 made for the retrieval
metrics.
"""

from __future__ import annotations

from collections.abc import Sequence

LABELS: tuple[float, ...] = (0.0, 0.5, 1.0)
_LABEL_INDEX = {label: i for i, label in enumerate(LABELS)}


def _round_to_label(score: float) -> float:
    """Snaps a raw score to the nearest of `LABELS` -- defensive rounding
    per the spec ("round 0.5 scores to a small fixed label set before
    computing agreement"), so a caller passing an unrounded float (or a
    judge score that's already exact) is handled uniformly."""
    return min(LABELS, key=lambda label: abs(label - score))


def confusion_matrix(
    human_labels: Sequence[float], judge_labels: Sequence[float]
) -> list[list[int]]:
    """3x3 matrix, rows=human label index, cols=judge label index, in
    `LABELS` order (0, 0.5, 1) -- the human-vs-judge table
    docs/judge_calibration.md reports."""
    if len(human_labels) != len(judge_labels):
        raise ValueError("human_labels and judge_labels must be the same length")
    matrix = [[0] * len(LABELS) for _ in LABELS]
    for h, j in zip(human_labels, judge_labels, strict=True):
        matrix[_LABEL_INDEX[_round_to_label(h)]][_LABEL_INDEX[_round_to_label(j)]] += 1
    return matrix


def cohens_kappa(human_labels: Sequence[float], judge_labels: Sequence[float]) -> float:
    """Linear-weighted Cohen's kappa over the ordinal {0, 0.5, 1} scale.

    kappa = 1 - (observed weighted disagreement) / (chance-expected weighted
    disagreement), with disagreement weight w[i][j] = |i - j| / (k - 1) so
    a same-cell match contributes 0 and the two most-distant labels
    contribute 1. Returns 1.0 in the degenerate case where both raters used
    only one (matching) label throughout, since expected disagreement is
    then 0 by construction and observed disagreement is necessarily 0 too.
    """
    matrix = confusion_matrix(human_labels, judge_labels)
    n = len(human_labels)
    if n == 0:
        raise ValueError("cohens_kappa requires at least one labeled pair")

    k = len(LABELS)
    weights = [[abs(i - j) / (k - 1) for j in range(k)] for i in range(k)]
    row_totals = [sum(row) for row in matrix]
    col_totals = [sum(matrix[i][j] for i in range(k)) for j in range(k)]

    observed = sum(weights[i][j] * matrix[i][j] for i in range(k) for j in range(k)) / n
    expected = sum(
        weights[i][j] * row_totals[i] * col_totals[j] for i in range(k) for j in range(k)
    ) / (n * n)

    if expected == 0:
        return 1.0
    return 1 - observed / expected
