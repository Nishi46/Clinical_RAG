"""Cross-encoder reranking -- S3-11's ladder rung 5, the final rung.

`rerank_ladder` retrieves a wider candidate pool via S3-09/S3-10's
prefiltered `hybrid_search`, then reranks it down to `top_k` with a real
cross-encoder (scores a `(query, chunk_text)` pair directly, rather than
comparing independently-embedded vectors -- slower per pair, but far more
accurate at judging relevance for a small candidate set, which is exactly
why it runs *after* the cheaper dense/lexical stages narrow the field
instead of over the whole corpus).
"""

from __future__ import annotations

from typing import Any

import psycopg

from protocol_drift.retrieval.hybrid import hybrid_search
from protocol_drift.retrieval.query_parse import QueryFilters
from protocol_drift.retrieval.types import RetrievedChunk
from protocol_drift.trace.store import TraceStore, traced_call

DEFAULT_CANDIDATE_K = 50
DEFAULT_TOP_K = 8

# Mirrors configs/models.yaml's `reranker` entry -- kept as a literal here
# (no config-loading utility exists yet in this codebase), same convention
# as S3-01/S3-06's DEFAULT_MODEL_NAME/DEFAULT_MODEL_REVISION constants.
DEFAULT_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
DEFAULT_MODEL_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


def load_reranker(model_name: str, revision: str) -> Any:
    """Loads the cross-encoder once. Import is deferred to call time so
    nothing outside the `retrieval` optional dependency group needs
    `sentence-transformers` importable just to import this module (same
    convention as embed.py::load_embedder)."""
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name, revision=revision)


def rerank(
    query: str,
    candidate_chunks: list[RetrievedChunk],
    reranker: Any,
    top_k: int = DEFAULT_TOP_K,
) -> list[str]:
    """Scores every (query, chunk.text) pair via `reranker.predict`, sorts
    descending, returns the top_k chunk_ids."""
    if not candidate_chunks:
        return []
    pairs = [(query, chunk.text) for chunk in candidate_chunks]
    scores = reranker.predict(pairs)
    ranked = sorted(
        zip(candidate_chunks, scores, strict=True), key=lambda pair: pair[1], reverse=True
    )
    return [chunk.chunk_id for chunk, _ in ranked[:top_k]]


def _fetch_chunks(conn: psycopg.Connection[Any], chunk_ids: list[str]) -> list[RetrievedChunk]:
    if not chunk_ids:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_id, text FROM chunks WHERE chunk_id = ANY(%s)", (chunk_ids,))
        text_by_id = dict(cur.fetchall())
    # Preserve hybrid_search's ranking order -- `= ANY(...)` makes no
    # ordering guarantee of its own.
    return [
        RetrievedChunk(chunk_id=chunk_id, text=text_by_id[chunk_id])
        for chunk_id in chunk_ids
        if chunk_id in text_by_id
    ]


def rerank_ladder(
    query: str,
    embedder: Any,
    reranker: Any,
    conn: psycopg.Connection[Any],
    store: TraceStore,
    query_id: int,
    filters: QueryFilters | None = None,
    k_candidates: int = DEFAULT_CANDIDATE_K,
    top_k: int = DEFAULT_TOP_K,
) -> list[str]:
    """Retrieves `k_candidates` via prefiltered hybrid_search (which
    self-traces its own prefilter/dense/bm25 sub-stages under this same
    query_id), then reranks to `top_k`, wrapped in its own
    `traced_call(..., "rerank")` -- isolating just the cross-encoder
    scoring latency, not the whole pipeline's, so S3-12's per-stage
    latency breakdown has a real, narrow number for this stage."""
    candidate_ids = hybrid_search(
        query, k_candidates, embedder, conn, store, query_id, filters=filters
    )
    candidate_chunks = _fetch_chunks(conn, candidate_ids)

    with traced_call(store, query_id, "rerank") as trace:
        reranked_ids = rerank(query, candidate_chunks, reranker, top_k=top_k)
        trace.chunk_hits = [
            {"chunk_id": chunk_id, "rank": rank} for rank, chunk_id in enumerate(reranked_ids)
        ]

    return reranked_ids
