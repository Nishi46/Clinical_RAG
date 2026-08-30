import psycopg
import pytest

from protocol_drift.retrieval.dense import dense_search
from protocol_drift.retrieval.query_parse import QueryFilters
from tests.retrieval.conftest import KNOWN_CHUNK_ID, OTHER_TRIAL_CHUNK_ID, OTHER_TRIAL_NCT_ID


def _embedding_for(conn: psycopg.Connection, chunk_id: str) -> list[float]:
    with conn.cursor() as cur:
        cur.execute("SELECT embedding FROM chunks WHERE chunk_id = %s", (chunk_id,))
        row = cur.fetchone()
    assert row is not None
    return list(row[0].to_list())


@pytest.mark.db
def test_dense_search_finds_exact_match_for_its_own_embedding(
    fixture_corpus: psycopg.Connection,
) -> None:
    query_embedding = _embedding_for(fixture_corpus, KNOWN_CHUNK_ID)

    results = dense_search(query_embedding, 5, fixture_corpus)

    assert results[0][0] == KNOWN_CHUNK_ID
    assert results[0][1] == pytest.approx(0.0, abs=1e-4)


@pytest.mark.db
def test_dense_search_orthogonal_chunk_is_maximally_distant(
    fixture_corpus: psycopg.Connection,
) -> None:
    query_embedding = _embedding_for(fixture_corpus, KNOWN_CHUNK_ID)

    # Filtered to the other fixture trial specifically: a locally-loaded dev
    # DB has the full real corpus alongside the fixture rows, and an
    # unfiltered top-5 would fill up with real chunks that happen to be
    # slightly closer than the deliberately-orthogonal fixture chunk.
    results = dense_search(
        query_embedding, 5, fixture_corpus, filters=QueryFilters(nct_id=OTHER_TRIAL_NCT_ID)
    )
    by_id = dict(results)

    assert by_id[OTHER_TRIAL_CHUNK_ID] == pytest.approx(1.0, abs=1e-4)


@pytest.mark.db
def test_dense_search_respects_k_limit(fixture_corpus: psycopg.Connection) -> None:
    query_embedding = _embedding_for(fixture_corpus, KNOWN_CHUNK_ID)
    results = dense_search(query_embedding, 1, fixture_corpus)
    assert len(results) <= 1


@pytest.mark.db
def test_dense_search_distances_are_ascending(fixture_corpus: psycopg.Connection) -> None:
    query_embedding = _embedding_for(fixture_corpus, KNOWN_CHUNK_ID)
    results = dense_search(query_embedding, 10, fixture_corpus)
    distances = [distance for _, distance in results]
    assert distances == sorted(distances)
