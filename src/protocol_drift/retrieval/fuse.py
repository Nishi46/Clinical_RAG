"""Reciprocal rank fusion -- S3-09.

Combines multiple rankings (e.g. dense + lexical) into one, using each
document's *rank* in each list rather than its raw score -- sidesteps the
problem that cosine distance and ts_rank_cd live on incomparable scales.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], k: int = DEFAULT_RRF_K
) -> list[tuple[str, float]]:
    """score(d) = sum over rankings containing d of 1 / (k + rank(d)), rank
    1-indexed. A document absent from a ranking contributes nothing from
    it. Returns every document that appears in at least one ranking,
    sorted by descending fused score."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
