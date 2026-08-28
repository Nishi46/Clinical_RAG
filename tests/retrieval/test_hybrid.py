from collections.abc import Iterator

import psycopg
import pytest
from pgvector.psycopg import register_vector

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.retrieval.hybrid import hybrid_search
from protocol_drift.trace.store import TraceStore

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
    """Returns the real stored embedding for KNOWN_CHUNK_ID regardless of
    input text, so the dense leg of hybrid_search deterministically ranks
    that chunk first -- avoids needing the real sentence-transformers model
    just to exercise the fusion plumbing."""

    def __init__(self, conn: psycopg.Connection) -> None:
        with conn.cursor() as cur:
            cur.execute("SELECT embedding FROM chunks WHERE chunk_id = %s", (KNOWN_CHUNK_ID,))
            row = cur.fetchone()
        assert row is not None
        self._vector = row[0].to_list()

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._vector for _ in texts]


@pytest.mark.db
def test_hybrid_search_fuses_dense_and_lexical_results(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder(conn)

    results = hybrid_search(
        "stereotactic radiation therapy SBRT", 10, embedder, conn, store, query_id
    )

    # Both legs should surface the known chunk (dense: it IS that chunk's
    # embedding; lexical: the query is a distinctive phrase from its text),
    # so RRF should rank it at or near the top.
    assert KNOWN_CHUNK_ID in results
    assert results.index(KNOWN_CHUNK_ID) == 0


@pytest.mark.db
def test_hybrid_search_traces_dense_and_bm25_substages(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    before = _max_ids(conn)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder(conn)

    hybrid_search("stereotactic radiation therapy SBRT", 10, embedder, conn, store, query_id)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT stage FROM retrieval_step WHERE query_id = %s AND id > %s ORDER BY id",
            (query_id, before["retrieval_step"]),
        )
        stages = [row[0] for row in cur.fetchall()]
    assert stages == ["dense", "bm25"]


@pytest.mark.db
def test_hybrid_search_respects_k(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query("cancer treatment patient eligibility")
    embedder = _FakeEmbedder(conn)

    results = hybrid_search(
        "cancer treatment patient eligibility", 3, embedder, conn, store, query_id
    )

    assert len(results) <= 3
