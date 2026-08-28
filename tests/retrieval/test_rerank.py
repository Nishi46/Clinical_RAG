"""rerank/rerank_ladder tests. `rerank` itself is pure (given a mocked
cross-encoder) and runs in the fast path. `rerank_ladder` drives real
hybrid_search + a real trace store and is marked `db`.
"""

from collections.abc import Iterator

import psycopg
import pytest
from pgvector.psycopg import register_vector

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.retrieval.rerank import rerank, rerank_ladder
from protocol_drift.retrieval.types import RetrievedChunk
from protocol_drift.trace.store import TraceStore

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

KNOWN_CHUNK_ID = "NCT03007407:protocol:19"

_TABLES_CHILD_TO_PARENT = ("cost_record", "generation", "chunk_hit", "retrieval_step", "query")


def _max_ids(conn: psycopg.Connection) -> dict[str, int]:
    ids: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in _TABLES_CHILD_TO_PARENT:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
            row = cur.fetchone()
            ids[table] = row[0] if row else 0
    return ids


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    register_vector(connection)
    before_ids = _max_ids(connection)
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        for table in _TABLES_CHILD_TO_PARENT:
            cur.execute(f"DELETE FROM {table} WHERE id > %s", (before_ids[table],))
    connection.commit()
    connection.close()


class _FakeEmbedder:
    def __init__(self, conn: psycopg.Connection) -> None:
        with conn.cursor() as cur:
            cur.execute("SELECT embedding FROM chunks WHERE chunk_id = %s", (KNOWN_CHUNK_ID,))
            row = cur.fetchone()
        assert row is not None
        self._vector = row[0].to_list()

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._vector for _ in texts]


class _RewardsKnownChunkReranker:
    """Scores the known chunk highest, everything else uniformly low --
    deterministic without needing the real cross-encoder model."""

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [1.0 if KNOWN_CHUNK_ID in text else 0.0 for _, text in pairs]


@pytest.mark.db
def test_rerank_ladder_returns_top_k_from_hybrid_candidates(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder(conn)
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
    conn: psycopg.Connection,
) -> None:
    store = TraceStore(conn)
    before = _max_ids(conn)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder(conn)
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
    conn: psycopg.Connection,
) -> None:
    store = TraceStore(conn)
    before = _max_ids(conn)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder(conn)
    reranker = _RewardsKnownChunkReranker()

    rerank_ladder(
        "stereotactic radiation therapy SBRT", embedder, reranker, conn, store, query_id, top_k=3
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM retrieval_step "
            "WHERE query_id = %s AND stage = 'rerank' AND id > %s",
            (query_id, before["retrieval_step"]),
        )
        row = cur.fetchone()
        assert row is not None
        step_id = row[0]
        cur.execute("SELECT count(*) FROM chunk_hit WHERE retrieval_step_id = %s", (step_id,))
        row = cur.fetchone()
        assert row is not None and row[0] == 3
