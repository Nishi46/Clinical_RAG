from collections.abc import Iterator

import psycopg
import pytest
from pgvector.psycopg import register_vector

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.retrieval.dense import dense_search

KNOWN_CHUNK_ID = "NCT03007407:protocol:19"


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    register_vector(connection)
    yield connection
    connection.close()


def _embedding_for(conn: psycopg.Connection, chunk_id: str) -> list[float]:
    with conn.cursor() as cur:
        cur.execute("SELECT embedding FROM chunks WHERE chunk_id = %s", (chunk_id,))
        row = cur.fetchone()
    assert row is not None
    return list(row[0].to_list())


@pytest.mark.db
def test_dense_search_finds_exact_match_for_its_own_embedding(conn: psycopg.Connection) -> None:
    query_embedding = _embedding_for(conn, KNOWN_CHUNK_ID)

    results = dense_search(query_embedding, 5, conn)

    assert results[0][0] == KNOWN_CHUNK_ID
    assert results[0][1] == pytest.approx(0.0, abs=1e-4)


@pytest.mark.db
def test_dense_search_respects_k_limit(conn: psycopg.Connection) -> None:
    query_embedding = _embedding_for(conn, KNOWN_CHUNK_ID)
    results = dense_search(query_embedding, 3, conn)
    assert len(results) <= 3


@pytest.mark.db
def test_dense_search_distances_are_ascending(conn: psycopg.Connection) -> None:
    query_embedding = _embedding_for(conn, KNOWN_CHUNK_ID)
    results = dense_search(query_embedding, 10, conn)
    distances = [distance for _, distance in results]
    assert distances == sorted(distances)
