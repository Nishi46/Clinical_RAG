"""Shared fixtures for tests/retrieval/'s db-marked tests.

`data/` (the real ingested corpus) is entirely gitignored -- a fresh CI
checkout has an empty database, schema only. These fixtures insert a small,
fully synthetic trial + chunk set with controlled, known text and
embeddings, so retrieval tests pass against *any* schema-populated
Postgres, not just a locally fully-loaded dev database.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from pgvector import Vector
from pgvector.psycopg import register_vector

from protocol_drift.db import DEFAULT_DSN as DSN

FIXTURE_NCT_ID = "NCT99999999"
OTHER_TRIAL_NCT_ID = "NCT99999998"
KNOWN_CHUNK_ID = f"{FIXTURE_NCT_ID}:protocol:0"
OTHER_TRIAL_CHUNK_ID = f"{OTHER_TRIAL_NCT_ID}:protocol:0"

# Covers every phrase these tests' lexical queries search for ("stereotactic
# radiation therapy SBRT lung liver metastatic", "cancer treatment patient
# eligibility") in one chunk, plus a marker string reranker tests can key on
# without depending on the literal chunk_id appearing inside chunk text
# (which it never does in the real corpus either -- the S2-08 contextual
# header embeds nct_id/doc_type/section, not the chunk_index suffix).
KNOWN_CHUNK_TEXT = (
    "This protocol describes stereotactic radiation therapy (SBRT) for lung and "
    "liver metastatic lesions as part of cancer treatment. Patients must meet "
    "eligibility criteria to enroll in this clinical trial. UNIQUE-KNOWN-CHUNK-MARKER."
)
OTHER_CHUNK_TEXT = "Unrelated filler text about a completely different subject entirely."
KNOWN_CHUNK_MARKER = "UNIQUE-KNOWN-CHUNK-MARKER"

_TABLES_CHILD_TO_PARENT = ("cost_record", "generation", "chunk_hit", "retrieval_step", "query")

_INSERT_CHUNK = """
    INSERT INTO chunks (
        chunk_id, nct_id, doc_type, doc_version, section,
        chunk_type, is_ocr, text, embedding, embedding_cache_key
    )
    VALUES (%s, %s, 'protocol', 1, 'eligibility', 'text', FALSE, %s, %s, %s)
"""


def _embedding(hot_index: int) -> list[float]:
    """A 768-dim one-hot vector. Two different hot_index values are always
    orthogonal (cosine distance 1.0); the same hot_index is an exact match
    (cosine distance 0.0) -- deterministic dense_search behavior with no
    real embedding model needed."""
    vector = [0.0] * 768
    vector[hot_index] = 1.0
    return vector


KNOWN_CHUNK_EMBEDDING = _embedding(0)
OTHER_CHUNK_EMBEDDING = _embedding(1)


def max_ids(conn: psycopg.Connection) -> dict[str, int]:
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
    before_ids = max_ids(connection)
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        for table in _TABLES_CHILD_TO_PARENT:
            cur.execute(f"DELETE FROM {table} WHERE id > %s", (before_ids[table],))
        cur.execute(
            "DELETE FROM chunks WHERE nct_id IN (%s, %s)", (FIXTURE_NCT_ID, OTHER_TRIAL_NCT_ID)
        )
        cur.execute(
            "DELETE FROM trials WHERE nct_id IN (%s, %s)", (FIXTURE_NCT_ID, OTHER_TRIAL_NCT_ID)
        )
    connection.commit()
    connection.close()


@pytest.fixture
def fixture_corpus(conn: psycopg.Connection) -> psycopg.Connection:
    """Two trials, one chunk each -- KNOWN_CHUNK_ID (rich, distinctive text
    + KNOWN_CHUNK_EMBEDDING) in FIXTURE_NCT_ID, and an unrelated filler
    chunk in a second trial (OTHER_TRIAL_NCT_ID) so nct_id-prefiltering
    tests have a real "wrong trial" to confirm exclusion against."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trials (nct_id, brief_title) VALUES (%s, 'Fixture Trial'), "
            "(%s, 'Other Fixture Trial')",
            (FIXTURE_NCT_ID, OTHER_TRIAL_NCT_ID),
        )
        cur.execute(
            _INSERT_CHUNK,
            (KNOWN_CHUNK_ID, FIXTURE_NCT_ID, KNOWN_CHUNK_TEXT, Vector(KNOWN_CHUNK_EMBEDDING), "k1"),
        )
        cur.execute(
            _INSERT_CHUNK,
            (
                OTHER_TRIAL_CHUNK_ID,
                OTHER_TRIAL_NCT_ID,
                OTHER_CHUNK_TEXT,
                Vector(OTHER_CHUNK_EMBEDDING),
                "k2",
            ),
        )
    conn.commit()
    return conn
