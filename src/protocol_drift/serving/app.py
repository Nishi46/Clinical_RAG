"""FastAPI + SSE serving app -- S5-01.

`/answer` shares S3-11's `rerank_ladder`, S4-04's `answer_cross_source_query`,
and S3-06/this sprint's `stream_answer` with the eval harness -- a query
answered through this API calls the exact same retrieval/generation
functions a batch eval run does, not a parallel reimplementation, so it
produces the same trace-store rows.

SSE is hand-rolled over `StreamingResponse` (`text/event-stream`) rather than
via `sse-starlette`, per `project_plan.md` §11's "Plain Python" stack choice
-- one more dependency isn't needed for a handful of `data: ...\n\n` lines.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pgvector.psycopg import register_vector
from pydantic import BaseModel

from protocol_drift.db import DEFAULT_DSN
from protocol_drift.discrepancy.detector import DiscrepancyReport, detect_discrepancies
from protocol_drift.eval.models import EvalQuestion
from protocol_drift.generation.answer import stream_answer
from protocol_drift.retrieval import embed, rerank
from protocol_drift.retrieval.decompose import answer_cross_source_query
from protocol_drift.retrieval.query_parse import parse_query_filters
from protocol_drift.retrieval.rerank import rerank_ladder
from protocol_drift.retrieval.types import RetrievedChunk, fetch_chunks
from protocol_drift.trace.store import TraceStore

app = FastAPI(title="protocol-drift serving API")


class AnswerRequest(BaseModel):
    nct_id: str
    question: str
    tier: str | None = None


def get_connection() -> Iterator[psycopg.Connection[Any]]:
    """A fresh connection per request, closed once the (possibly streamed)
    response finishes -- FastAPI keeps a `yield`-dependency open for the
    lifetime of `StreamingResponse`'s body iteration, not just until the
    endpoint function returns."""
    conn = psycopg.connect(DEFAULT_DSN)
    register_vector(conn)
    try:
        yield conn
    finally:
        conn.close()


@lru_cache(maxsize=1)
def _embedder() -> Any:
    return embed.load_embedder(embed.DEFAULT_MODEL_NAME, embed.DEFAULT_MODEL_REVISION)


@lru_cache(maxsize=1)
def _reranker() -> Any:
    return rerank.load_reranker(rerank.DEFAULT_MODEL_NAME, rerank.DEFAULT_MODEL_REVISION)


def get_embedder() -> Any:
    return _embedder()


def get_reranker() -> Any:
    return _reranker()


def _sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _cross_source_chunks(
    nct_id: str,
    question: str,
    embedder: Any,
    reranker: Any,
    conn: psycopg.Connection[Any],
    store: TraceStore,
    query_id: int,
) -> list[RetrievedChunk]:
    """T3 leg: runs S4-04's structured/protocol decomposition and folds its
    three legs into `RetrievedChunk`s so `stream_answer` can build one
    numbered-excerpt prompt over them exactly as it does for a plain
    retrieval list -- registry legs get a synthetic chunk_id (no `chunks`
    row backs them; they're a direct DB lookup, not a retrieved chunk)."""
    cross_source = answer_cross_source_query(
        question, nct_id, embedder, conn, reranker, store, query_id
    )
    chunks: list[RetrievedChunk] = []
    if cross_source.protocol_leg is not None:
        chunks.append(
            RetrievedChunk(
                chunk_id=cross_source.protocol_chunk_id or f"{nct_id}:protocol",
                text=cross_source.protocol_leg,
            )
        )
    if cross_source.registered_first is not None:
        chunks.append(
            RetrievedChunk(
                chunk_id=f"{nct_id}:registered_first", text=cross_source.registered_first
            )
        )
    if cross_source.registered_current is not None:
        chunks.append(
            RetrievedChunk(
                chunk_id=f"{nct_id}:registered_current", text=cross_source.registered_current
            )
        )
    return chunks


def _answer_events(
    request: AnswerRequest,
    conn: psycopg.Connection[Any],
    embedder: Any,
    reranker: Any,
    store: TraceStore,
) -> Iterator[str]:
    question = EvalQuestion(
        question_id="api",
        nct_id=request.nct_id,
        question_text=request.question,
        gold_answer="",
        gold_chunk_ids=[],
    )
    query_id = store.log_query(request.question, tier=request.tier)

    if request.tier == "T3":
        chunks = _cross_source_chunks(
            request.nct_id, request.question, embedder, reranker, conn, store, query_id
        )
    else:
        filters = parse_query_filters(request.question, nct_id=request.nct_id)
        chunk_ids = rerank_ladder(
            request.question, embedder, reranker, conn, store, query_id, filters=filters
        )
        chunks = fetch_chunks(conn, chunk_ids)

    for event in stream_answer(question, chunks, store, tier=request.tier, query_id=query_id):
        yield _sse_event(event)


@app.post("/answer")
def answer(
    request: AnswerRequest,
    conn: psycopg.Connection[Any] = Depends(get_connection),
    embedder: Any = Depends(get_embedder),
    reranker: Any = Depends(get_reranker),
) -> StreamingResponse:
    store = TraceStore(conn)
    return StreamingResponse(
        _answer_events(request, conn, embedder, reranker, store),
        media_type="text/event-stream",
    )


@app.get("/health")
def health() -> JSONResponse:
    """Checks a live Postgres connection directly (not via the shared
    `get_connection` dependency) so a DB outage is reported as a 503 body
    instead of surfacing as an unhandled dependency-injection error."""
    try:
        conn = psycopg.connect(DEFAULT_DSN)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})
    return JSONResponse(status_code=200, content={"status": "ok"})


@app.get("/discrepancy/{nct_id}")
def discrepancy(
    nct_id: str,
    conn: psycopg.Connection[Any] = Depends(get_connection),
    embedder: Any = Depends(get_embedder),
    reranker: Any = Depends(get_reranker),
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM trials WHERE nct_id = %s", (nct_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"unknown nct_id: {nct_id}")

    store = TraceStore(conn)
    query_id = store.log_query(f"discrepancy report for {nct_id}", tier="discrepancy")
    report: DiscrepancyReport = detect_discrepancies(
        nct_id, conn, embedder, reranker, store, query_id
    )
    return report.to_dict()
