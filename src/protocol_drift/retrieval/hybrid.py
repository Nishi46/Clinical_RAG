"""Hybrid search -- S3-09's ladder rung 3 (dense + lexical, fused by RRF),
extended by S3-10 with an optional metadata prefilter (ladder rung 4).

Runs dense_search and lexical_search sequentially (per the sprint plan's
own note: not enough query volume here to need real concurrency), each
wrapped in its own `traced_call` sub-stage so the per-stage latency
breakdown S3-12's ablation table needs is available from real traces, not
hand-typed. The fused list itself is *not* separately traced here -- the
caller (typically `score_retrieval_run`) wraps the whole `hybrid_search`
call in its own `traced_call(..., "rrf")`, which already logs the final
returned ranking as this query's chunk_hits; a second "rrf" trace inside
this function would just duplicate that.

`filters` is expected to be "always on" in practice (per S3-10: this
system answers questions about one trial at a time, so the realistic case
is every query already knows its `nct_id`), not an occasional opt-in --
callers build it once (typically `QueryFilters(nct_id=question.nct_id)`)
and pass it straight through; `hybrid_search` itself does no text parsing
of its own (that's `query_parse.parse_query_filters`'s job, for callers
that don't already have the trial context).
"""

from __future__ import annotations

from typing import Any

import psycopg

from protocol_drift.retrieval.dense import dense_search, embed_query
from protocol_drift.retrieval.fuse import reciprocal_rank_fusion
from protocol_drift.retrieval.lexical import lexical_search
from protocol_drift.retrieval.query_parse import QueryFilters
from protocol_drift.trace.store import TraceStore, traced_call

DEFAULT_CANDIDATE_K = 50


def _describe_filters(filters: QueryFilters | None) -> str:
    if filters is None:
        return "none"
    fields = (
        ("nct_id", filters.nct_id),
        ("doc_type", filters.doc_type),
        ("doc_version", filters.doc_version),
    )
    fired = [f"{name}={value}" for name, value in fields if value is not None]
    return ",".join(fired) if fired else "none"


def hybrid_search(
    query: str,
    k: int,
    embedder: Any,
    conn: psycopg.Connection[Any],
    store: TraceStore,
    query_id: int,
    filters: QueryFilters | None = None,
    candidate_k: int = DEFAULT_CANDIDATE_K,
) -> list[str]:
    with traced_call(store, query_id, "prefilter") as trace:
        trace.filters_applied = _describe_filters(filters)

    with traced_call(store, query_id, "dense") as trace:
        query_embedding = embed_query(query, embedder)
        dense_results = dense_search(query_embedding, candidate_k, conn, filters=filters)
        trace.chunk_hits = [
            {"chunk_id": chunk_id, "rank": rank, "score": score}
            for rank, (chunk_id, score) in enumerate(dense_results)
        ]

    with traced_call(store, query_id, "bm25") as trace:
        lexical_results = lexical_search(query, candidate_k, conn, filters=filters)
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
