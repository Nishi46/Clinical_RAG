import psycopg
import pytest

from protocol_drift.retrieval.hybrid import hybrid_search
from protocol_drift.retrieval.query_parse import QueryFilters
from protocol_drift.trace.store import TraceStore
from tests.retrieval.conftest import (
    FIXTURE_NCT_ID,
    KNOWN_CHUNK_EMBEDDING,
    KNOWN_CHUNK_ID,
    OTHER_TRIAL_CHUNK_ID,
    max_ids,
)


class _FakeEmbedder:
    """Always returns KNOWN_CHUNK_EMBEDDING regardless of input text, so the
    dense leg of hybrid_search deterministically ranks the fixture's known
    chunk first -- avoids needing the real sentence-transformers model just
    to exercise the fusion plumbing."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [KNOWN_CHUNK_EMBEDDING for _ in texts]


@pytest.mark.db
def test_hybrid_search_fuses_dense_and_lexical_results(fixture_corpus: psycopg.Connection) -> None:
    store = TraceStore(fixture_corpus)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder()

    results = hybrid_search(
        "stereotactic radiation therapy SBRT", 10, embedder, fixture_corpus, store, query_id
    )

    # Both legs should surface the known chunk (dense: it IS that chunk's
    # embedding; lexical: the query is a distinctive phrase from its text),
    # so RRF should rank it at or near the top.
    assert KNOWN_CHUNK_ID in results
    assert results.index(KNOWN_CHUNK_ID) == 0


@pytest.mark.db
def test_hybrid_search_traces_prefilter_dense_and_bm25_substages(
    fixture_corpus: psycopg.Connection,
) -> None:
    store = TraceStore(fixture_corpus)
    before = max_ids(fixture_corpus)
    query_id = store.log_query("stereotactic radiation therapy SBRT")
    embedder = _FakeEmbedder()

    hybrid_search(
        "stereotactic radiation therapy SBRT", 10, embedder, fixture_corpus, store, query_id
    )

    with fixture_corpus.cursor() as cur:
        cur.execute(
            "SELECT stage FROM retrieval_step WHERE query_id = %s AND id > %s ORDER BY id",
            (query_id, before["retrieval_step"]),
        )
        stages = [row[0] for row in cur.fetchall()]
    assert stages == ["prefilter", "dense", "bm25"]


@pytest.mark.db
def test_hybrid_search_respects_k(fixture_corpus: psycopg.Connection) -> None:
    store = TraceStore(fixture_corpus)
    query_id = store.log_query("cancer treatment patient eligibility")
    embedder = _FakeEmbedder()

    results = hybrid_search(
        "cancer treatment patient eligibility", 1, embedder, fixture_corpus, store, query_id
    )

    assert len(results) <= 1


@pytest.mark.db
def test_hybrid_search_with_nct_id_filter_only_returns_that_trial(
    fixture_corpus: psycopg.Connection,
) -> None:
    store = TraceStore(fixture_corpus)
    query_id = store.log_query("cancer treatment patient eligibility")
    embedder = _FakeEmbedder()
    filters = QueryFilters(nct_id=FIXTURE_NCT_ID)

    results = hybrid_search(
        "cancer treatment patient eligibility",
        20,
        embedder,
        fixture_corpus,
        store,
        query_id,
        filters=filters,
    )

    assert results  # sanity: the filter didn't eliminate everything
    with fixture_corpus.cursor() as cur:
        cur.execute("SELECT DISTINCT nct_id FROM chunks WHERE chunk_id = ANY(%s)", (results,))
        nct_ids = {row[0] for row in cur.fetchall()}
    assert nct_ids == {FIXTURE_NCT_ID}
    assert OTHER_TRIAL_CHUNK_ID not in results


@pytest.mark.db
def test_hybrid_search_logs_filters_applied_on_prefilter_stage(
    fixture_corpus: psycopg.Connection,
) -> None:
    store = TraceStore(fixture_corpus)
    before = max_ids(fixture_corpus)
    query_id = store.log_query("cancer treatment patient eligibility")
    embedder = _FakeEmbedder()
    filters = QueryFilters(nct_id=FIXTURE_NCT_ID, doc_type="protocol")

    hybrid_search(
        "cancer treatment patient eligibility",
        5,
        embedder,
        fixture_corpus,
        store,
        query_id,
        filters=filters,
    )

    with fixture_corpus.cursor() as cur:
        cur.execute(
            "SELECT filters_applied FROM retrieval_step "
            "WHERE query_id = %s AND stage = 'prefilter' AND id > %s",
            (query_id, before["retrieval_step"]),
        )
        row = cur.fetchone()
    assert row is not None
    assert f"nct_id={FIXTURE_NCT_ID}" in row[0]
    assert "doc_type=protocol" in row[0]


@pytest.mark.db
def test_hybrid_search_no_filters_logs_none_on_prefilter_stage(
    fixture_corpus: psycopg.Connection,
) -> None:
    store = TraceStore(fixture_corpus)
    before = max_ids(fixture_corpus)
    query_id = store.log_query("cancer treatment patient eligibility")
    embedder = _FakeEmbedder()

    hybrid_search(
        "cancer treatment patient eligibility", 5, embedder, fixture_corpus, store, query_id
    )

    with fixture_corpus.cursor() as cur:
        cur.execute(
            "SELECT filters_applied FROM retrieval_step "
            "WHERE query_id = %s AND stage = 'prefilter' AND id > %s",
            (query_id, before["retrieval_step"]),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "none"
