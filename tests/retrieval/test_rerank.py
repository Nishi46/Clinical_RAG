"""rerank/rerank_ladder tests. `rerank` itself is pure (given a mocked
cross-encoder) and runs in the fast path. `rerank_ladder` drives real
hybrid_search + a real trace store and is marked `db`.
"""

import psycopg
import pytest

from protocol_drift.retrieval.rerank import rerank, rerank_ladder
from protocol_drift.retrieval.types import RetrievedChunk
from protocol_drift.trace.store import TraceStore
from tests.retrieval.conftest import (
    KNOWN_CHUNK_EMBEDDING,
    KNOWN_CHUNK_ID,
    KNOWN_CHUNK_MARKER,
    max_ids,
)

CHUNKS = [
    RetrievedChunk(chunk_id="c1", text="low relevance"),
    RetrievedChunk(chunk_id="c2", text="high relevance"),
    RetrievedChunk(chunk_id="c3", text="medium relevance"),
    RetrievedChunk(chunk_id="c4", text="lowest relevance"),
]


class _FixedScoreReranker:
    """Mocked cross-encoder: assigns a score by chunk_id, ignoring the
    actual (query, text) pair content -- lets the test assert exact
    ordering without needing the real bge-reranker-v2-m3 model."""

    def __init__(self, scores_by_chunk_id: dict[str, float]) -> None:
        self._scores = scores_by_chunk_id

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        # Map back to chunk_id via the CHUNKS fixture's text, since predict
        # only receives (query, text) pairs, not the chunk objects.
        text_to_id = {c.text: c.chunk_id for c in CHUNKS}
        return [self._scores[text_to_id[text]] for _, text in pairs]


def test_rerank_returns_top_k_in_descending_mocked_score_order() -> None:
    reranker = _FixedScoreReranker({"c1": 0.1, "c2": 0.9, "c3": 0.5, "c4": 0.05})

    result = rerank("some query", CHUNKS, reranker, top_k=2)

    assert result == ["c2", "c3"]


def test_rerank_returns_exactly_top_k_even_with_more_candidates() -> None:
    reranker = _FixedScoreReranker({"c1": 0.4, "c2": 0.9, "c3": 0.5, "c4": 0.05})

    result = rerank("some query", CHUNKS, reranker, top_k=3)

    assert len(result) == 3
    assert result == ["c2", "c3", "c1"]


def test_rerank_empty_candidates_returns_empty_without_calling_predict() -> None:
    class _ExplodingReranker:
        def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
            raise AssertionError("predict should not be called for an empty candidate list")

    assert rerank("some query", [], _ExplodingReranker(), top_k=8) == []


def test_rerank_default_top_k_is_eight() -> None:
    many_chunks = [RetrievedChunk(chunk_id=f"c{i}", text=f"text {i}") for i in range(20)]
    reranker = _FixedScoreReranker({})
    reranker.predict = lambda pairs: [float(i) for i in range(len(pairs))]  # type: ignore[method-assign]

    result = rerank("some query", many_chunks, reranker)

    assert len(result) == 8


# --- rerank_ladder: real hybrid_search + real trace store ------------------


class _FakeEmbedder:
    """Always returns KNOWN_CHUNK_EMBEDDING regardless of input text, so the
    dense leg of hybrid_search deterministically surfaces the fixture's
    known chunk as a rerank candidate -- avoids needing the real
    sentence-transformers model."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [KNOWN_CHUNK_EMBEDDING for _ in texts]


class _RewardsKnownChunkReranker:
    """Scores the known chunk highest, everything else uniformly low --
    deterministic without needing the real cross-encoder model. Keys off
    KNOWN_CHUNK_MARKER (a literal string planted in the fixture's chunk
    text), not KNOWN_CHUNK_ID -- chunk_id strings never appear inside real
    chunk text (the S2-08 contextual header embeds nct_id/doc_type/section,
    not the chunk_index suffix), so checking for the id itself would pass
    for the wrong reason (stable-sort preserving pre-existing order)."""

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [1.0 if KNOWN_CHUNK_MARKER in text else 0.0 for _, text in pairs]


@pytest.mark.db
def test_rerank_ladder_returns_top_k_from_hybrid_candidates(
    fixture_corpus: psycopg.Connection,
) -> None:
    conn = fixture_corpus
    store = TraceStore(conn)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder()
    reranker = _RewardsKnownChunkReranker()

    results = rerank_ladder(
        "stereotactic radiation therapy SBRT",
        embedder,
        reranker,
        conn,
        store,
        query_id,
        top_k=5,
    )

    assert len(results) <= 5
    assert results[0] == KNOWN_CHUNK_ID


@pytest.mark.db
def test_rerank_ladder_traces_full_prefilter_dense_bm25_rerank_pipeline(
    fixture_corpus: psycopg.Connection,
) -> None:
    conn = fixture_corpus
    store = TraceStore(conn)
    before = max_ids(conn)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder()
    reranker = _RewardsKnownChunkReranker()

    rerank_ladder(
        "stereotactic radiation therapy SBRT", embedder, reranker, conn, store, query_id, top_k=5
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT stage FROM retrieval_step WHERE query_id = %s AND id > %s ORDER BY id",
            (query_id, before["retrieval_step"]),
        )
        stages = [row[0] for row in cur.fetchall()]
    assert stages == ["prefilter", "dense", "bm25", "rerank"]


@pytest.mark.db
def test_rerank_ladder_rerank_stage_has_its_own_narrow_chunk_hits(
    fixture_corpus: psycopg.Connection,
) -> None:
    conn = fixture_corpus
    store = TraceStore(conn)
    before = max_ids(conn)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder()
    reranker = _RewardsKnownChunkReranker()

    rerank_ladder(
        "stereotactic radiation therapy SBRT", embedder, reranker, conn, store, query_id, top_k=1
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM retrieval_step WHERE query_id = %s AND stage = 'rerank' AND id > %s",
            (query_id, before["retrieval_step"]),
        )
        row = cur.fetchone()
        assert row is not None
        step_id = row[0]
        cur.execute("SELECT count(*) FROM chunk_hit WHERE retrieval_step_id = %s", (step_id,))
        row = cur.fetchone()
        assert row is not None and row[0] == 1
