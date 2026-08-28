from collections.abc import Iterator

import psycopg
import pytest

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.retrieval.lexical import lexical_search

# A real, distinctive chunk in the loaded corpus (S3-02) -- confirmed by
# direct query, not asserted blind.
KNOWN_CHUNK_ID = "NCT03007407:protocol:19"


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    yield connection
    connection.close()


@pytest.mark.db
def test_lexical_search_finds_known_chunk_by_distinctive_phrase(
    conn: psycopg.Connection,
) -> None:
    results = lexical_search("stereotactic radiation therapy SBRT lung liver metastatic", 10, conn)
    assert KNOWN_CHUNK_ID in [chunk_id for chunk_id, _ in results]


@pytest.mark.db
def test_lexical_search_respects_k_limit(conn: psycopg.Connection) -> None:
    results = lexical_search("cancer treatment patient eligibility", 3, conn)
    assert len(results) <= 3


@pytest.mark.db
def test_lexical_search_scores_are_descending(conn: psycopg.Connection) -> None:
    results = lexical_search("cancer treatment patient eligibility criteria protocol", 20, conn)
    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.db
def test_lexical_search_no_match_returns_empty(conn: psycopg.Connection) -> None:
    results = lexical_search("xyzzyquantumflorbnonexistentgibberishtermzzz", 10, conn)
    assert results == []
