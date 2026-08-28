"""Hybrid search -- S3-09's ladder rung 3: dense + lexical, fused by RRF.

Runs dense_search and lexical_search sequentially (per the sprint plan's
own note: not enough query volume here to need real concurrency), each
wrapped in its own `traced_call` sub-stage so the per-stage latency
breakdown S3-12's ablation table needs is available from real traces, not
hand-typed. The fused list itself is *not* separately traced here -- the
caller (typically `score_retrieval_run`) wraps the whole `hybrid_search`
call in its own `traced_call(..., "rrf")`, which already logs the final
returned ranking as this query's chunk_hits; a second "rrf" trace inside
this function would just duplicate that.
"""

from __future__ import annotations

from typing import Any

import psycopg

from protocol_drift.retrieval.dense import dense_search, embed_query
from protocol_drift.retrieval.fuse import reciprocal_rank_fusion
from protocol_drift.retrieval.lexical import lexical_search
from protocol_drift.trace.store import TraceStore, traced_call

DEFAULT_CANDIDATE_K = 50


def hybrid_search(
    query: str,
    k: int,
    embedder: Any,
    conn: psycopg.Connection[Any],
    store: TraceStore,
    query_id: int,
    candidate_k: int = DEFAULT_CANDIDATE_K,
) -> list[str]:
    with traced_call(store, query_id, "dense") as trace:
        query_embedding = embed_query(query, embedder)
        dense_results = dense_search(query_embedding, candidate_k, conn)
        trace.chunk_hits = [
            {"chunk_id": chunk_id, "rank": rank, "score": score}
            for rank, (chunk_id, score) in enumerate(dense_results)
        ]

    with traced_call(store, query_id, "bm25") as trace:
        lexical_results = lexical_search(query, candidate_k, conn)
        trace.chunk_hits = [
            {"chunk_id": chunk_id, "rank": rank, "score": score}
            for rank, (chunk_id, score) in enumerate(lexical_results)
        ]

    fused = reciprocal_rank_fusion(
        [
            [chunk_id for chunk_id, _ in dense_results],
            [chunk_id for chunk_id, _ in lexical_results],
        ]
    )
    return [chunk_id for chunk_id, _ in fused[:k]]
