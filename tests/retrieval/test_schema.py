"""Schema tests against a real Postgres (protocol_drift_dev locally, or
whatever PROTOCOL_DRIFT_DSN points at in CI's service container).

Marked `db`, same convention as tests/trace/test_store.py: excluded from the
local `make test` fast path, run in CI. Inserts one throwaway row (a
"TEST:..." chunk_id and a real cohort nct_id, so the chunks.nct_id FK is
satisfied) and deletes it in teardown -- never touches the real loaded
corpus.
"""

from collections.abc import Iterator

import psycopg
import pytest
from pgvector import Vector
from pgvector.psycopg import register_vector

from protocol_drift.db import DEFAULT_DSN as DSN

_TEST_CHUNK_ID = "TEST:schema_test:0"
_TEST_NCT_ID = "NCT03007407"  # confirmed real row in trials (tests/ingestion/test_sections.py)

_INSERT_TEST_CHUNK = """
    INSERT INTO chunks (
        chunk_id, nct_id, doc_type, doc_version, section, subsection,
        page_range, chunk_type, is_ocr, text, embedding, embedding_cache_key
    )
    VALUES (%s, %s, 'protocol', 1, 'eligibility', NULL, '1-1', 'text', FALSE, %s, %s, 'cache-key')
"""


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    register_vector(connection)
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE chunk_id = %s", (_TEST_CHUNK_ID,))
    connection.commit()
    connection.close()


@pytest.mark.db
def test_insert_populates_text_search_automatically(conn: psycopg.Connection) -> None:
    text = "Patients must be at least 18 years of age to enroll."
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_TEST_CHUNK,
            (_TEST_CHUNK_ID, _TEST_NCT_ID, text, Vector([0.1] * 768)),
        )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT text_search @@ plainto_tsquery('english', 'patients enroll') "
            "FROM chunks WHERE chunk_id = %s",
            (_TEST_CHUNK_ID,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is True


@pytest.mark.db
def test_dense_query_uses_hnsw_index(conn: psycopg.Connection) -> None:
    filler_text = "filler text for the index-usage check"
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_TEST_CHUNK,
            (_TEST_CHUNK_ID, _TEST_NCT_ID, filler_text, Vector([0.1] * 768)),
        )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "EXPLAIN SELECT chunk_id FROM chunks ORDER BY embedding <=> %s LIMIT 5",
            (Vector([0.1] * 768),),
        )
        plan = "\n".join(row[0] for row in cur.fetchall())
    assert "idx_chunks_embedding_hnsw" in plan


@pytest.mark.db
def test_lexical_query_uses_gin_index(conn: psycopg.Connection) -> None:
    query = (
        "EXPLAIN SELECT chunk_id FROM chunks "
        "WHERE text_search @@ plainto_tsquery('english', 'eligibility criteria')"
    )
    with conn.cursor() as cur:
        cur.execute(query)
        plan = "\n".join(row[0] for row in cur.fetchall())
    assert "idx_chunks_text_search" in plan
