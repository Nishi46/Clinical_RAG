import psycopg
import pytest

from protocol_drift.retrieval.lexical import lexical_search
from tests.retrieval.conftest import KNOWN_CHUNK_ID


@pytest.mark.db
def test_lexical_search_finds_known_chunk_by_distinctive_phrase(
    fixture_corpus: psycopg.Connection,
) -> None:
    results = lexical_search(
        "stereotactic radiation therapy SBRT lung liver metastatic", 10, fixture_corpus
    )
    assert KNOWN_CHUNK_ID in [chunk_id for chunk_id, _ in results]


@pytest.mark.db
def test_lexical_search_respects_k_limit(fixture_corpus: psycopg.Connection) -> None:
    results = lexical_search("cancer treatment patient eligibility", 3, fixture_corpus)
    assert len(results) <= 3


@pytest.mark.db
def test_lexical_search_scores_are_descending(fixture_corpus: psycopg.Connection) -> None:
    results = lexical_search(
        "cancer treatment patient eligibility criteria protocol", 20, fixture_corpus
    )
    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.db
def test_lexical_search_no_match_returns_empty(fixture_corpus: psycopg.Connection) -> None:
    results = lexical_search("xyzzyquantumflorbnonexistentgibberishtermzzz", 10, fixture_corpus)
    assert results == []
