"""Trace store — every query, retrieval step, generation, and cost record.

No real model calls exist yet in Sprint 1; this exists so Sprint 2 onward has
somewhere to write from day one, per sprint_plan.md's acceptance criterion:
"every model call routes through a traced client." Each ``log_*`` method
commits immediately rather than batching — a trace store that loses writes
in an uncommitted transaction on a crash mid-eval-run defeats its own
purpose.

``traced_call`` is scoped to retrieval steps specifically (the stage
vocabulary -- dense/bm25/rrf/prefilter/rerank -- is retrieval-only), since
that is the concretely-specified, uniform, repeated use case: Sprint 3's
five retrieval-ladder rungs all call it the same way. Generation has a
different natural shape (it produces one text response, not a ranked list of
chunk hits) and is logged directly via ``log_generation`` at its own call
site instead of forcing a mismatched abstraction onto it.

``compute_prompt_hash`` fixes the caching key contract now (sha256 of model
digest + prompt text) so Sprint 3's "cache everything, keyed on model digest
+ prompt hash" requirement (sprint_plan.md appendix) has something stable to
build against, even though nothing hashes a real prompt yet.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg


def compute_prompt_hash(model_digest: str, prompt_text: str) -> str:
    return hashlib.sha256(f"{model_digest}:{prompt_text}".encode()).hexdigest()


class TraceStore:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def log_query(self, text: str, tier: str | None = None) -> int:
        return self._insert_returning_id(
            "INSERT INTO query (text, tier) VALUES (%s, %s) RETURNING id", (text, tier)
        )

    def log_retrieval_step(self, query_id: int, stage: str, latency_ms: float) -> int:
        return self._insert_returning_id(
            "INSERT INTO retrieval_step (query_id, stage, latency_ms) "
            "VALUES (%s, %s, %s) RETURNING id",
            (query_id, stage, latency_ms),
        )

    def log_chunk_hit(
        self,
        retrieval_step_id: int,
        chunk_id: str,
        rank: int | None = None,
        score: float | None = None,
        nct_id: str | None = None,
        doc_type: str | None = None,
        section: str | None = None,
        page_range: str | None = None,
    ) -> int:
        return self._insert_returning_id(
            "INSERT INTO chunk_hit "
            "(retrieval_step_id, chunk_id, rank, score, nct_id, doc_type, section, page_range) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (retrieval_step_id, chunk_id, rank, score, nct_id, doc_type, section, page_range),
        )

    def log_generation(
        self,
        query_id: int,
        model_digest: str,
        prompt_hash: str,
        response_text: str,
        latency_ms: float,
        token_count: int | None = None,
    ) -> int:
        return self._insert_returning_id(
            "INSERT INTO generation "
            "(query_id, model_digest, prompt_hash, response_text, latency_ms, token_count) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (query_id, model_digest, prompt_hash, response_text, latency_ms, token_count),
        )

    def log_cost(
        self, generation_id: int, tokens_in: int, tokens_out: int, wall_clock_ms: float
    ) -> int:
        return self._insert_returning_id(
            "INSERT INTO cost_record (generation_id, tokens_in, tokens_out, wall_clock_ms) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (generation_id, tokens_in, tokens_out, wall_clock_ms),
        )

    def find_generation(self, model_digest: str, prompt_hash: str) -> dict[str, Any] | None:
        """The most recent generation logged under this exact (model_digest,
        prompt_hash) pair, joined against its cost_record -- the read side
        of the "cache everything, keyed on model digest + prompt hash"
        contract `compute_prompt_hash` was written for. `None` on a cache
        miss. Callers reusing a cached `response_text` should still log a
        fresh generation/cost row for their own query_id (every call stays
        traced), just with zero new tokens/wall-clock time -- the original
        row already carries the real cost of producing this response."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT g.id, g.query_id, g.response_text, g.token_count, "
                "c.tokens_in, c.tokens_out "
                "FROM generation g LEFT JOIN cost_record c ON c.generation_id = g.id "
                "WHERE g.model_digest = %s AND g.prompt_hash = %s "
                "ORDER BY g.id DESC LIMIT 1",
                (model_digest, prompt_hash),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "query_id": row[1],
            "response_text": row[2],
            "token_count": row[3],
            "tokens_in": row[4],
            "tokens_out": row[5],
        }

    def _insert_returning_id(self, sql: str, params: tuple[Any, ...]) -> int:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        self._conn.commit()
        assert row is not None
        return int(row[0])


@dataclass
class RetrievalStepTrace:
    """Mutable accumulator yielded by ``traced_call``. Populate `chunk_hits`
    inside the `with` block; the step and every chunk hit are logged
    automatically on exit, timed around the whole block."""

    chunk_hits: list[dict[str, Any]] = field(default_factory=list)


@contextmanager
def traced_call(store: TraceStore, query_id: int, stage: str) -> Iterator[RetrievalStepTrace]:
    """Times one retrieval stage and logs a retrieval_step + its chunk_hits.

    Usage (illustrative -- Sprint 3's retrieval ladder rungs use this shape)::

        with traced_call(store, query_id, "dense") as trace:
            results = dense_search(query_embedding)
            trace.chunk_hits = [
                {"chunk_id": r.id, "rank": i, "score": r.score} for i, r in enumerate(results)
            ]
    """
    trace = RetrievalStepTrace()
    start = time.monotonic()
    try:
        yield trace
    finally:
        latency_ms = (time.monotonic() - start) * 1000
        step_id = store.log_retrieval_step(query_id, stage, latency_ms)
        for hit in trace.chunk_hits:
            store.log_chunk_hit(step_id, **hit)
