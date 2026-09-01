"""decompose_cross_source_query / answer_cross_source_query tests -- S4-04.
`decompose_cross_source_query` is pure and runs in the fast path.
`answer_cross_source_query` drives a real rerank_ladder + trace store and
is marked `db`, same convention as tests/retrieval/test_rerank.py -- a
fake embedder/reranker stand in for the real (heavy) models, same fixture
shapes tests/retrieval/conftest.py already established.
"""

from collections.abc import Iterator

import psycopg
import pytest
from pgvector import Vector
from pgvector.psycopg import register_vector

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.retrieval.decompose import (
    answer_cross_source_query,
    decompose_cross_source_query,
)
from protocol_drift.retrieval.query_parse import QueryFilters
from protocol_drift.trace.store import TraceStore
from tests.retrieval.conftest import max_ids

_WITH_OBJECTIVES = "NCT99999901"  # has an 'objectives'-section protocol chunk
_WITHOUT_OBJECTIVES = "NCT99999902"  # has a protocol chunk, but not in 'objectives'

_OBJECTIVES_CHUNK_ID = f"{_WITH_OBJECTIVES}:protocol:0"
_OBJECTIVES_CHUNK_MARKER = "UNIQUE-OBJECTIVES-CHUNK-MARKER"
_OBJECTIVES_CHUNK_TEXT = (
    f"The primary objective of this study is to evaluate overall survival. "
    f"{_OBJECTIVES_CHUNK_MARKER}."
)
_OTHER_SECTION_CHUNK_TEXT = "Eligible participants must be 18 years or older."

_FIRST_MEASURE = "Overall survival (OS)"
_CURRENT_MEASURE = "Overall survival (OS), CPS >= 5"


def _embedding(hot_index: int) -> list[float]:
    vector = [0.0] * 768
    vector[hot_index] = 1.0
    return vector


_OBJECTIVES_EMBEDDING = _embedding(0)
_OTHER_EMBEDDING = _embedding(1)


class _FakeEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [_OBJECTIVES_EMBEDDING for _ in texts]


class _RewardsObjectivesChunkReranker:
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [1.0 if _OBJECTIVES_CHUNK_MARKER in text else 0.0 for _, text in pairs]


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    register_vector(connection)
    before = max_ids(connection)
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        for table in ("cost_record", "generation", "chunk_hit", "retrieval_step", "query"):
            cur.execute(f"DELETE FROM {table} WHERE id > %s", (before[table],))
        cur.execute(
            "DELETE FROM outcomes WHERE nct_id IN (%s, %s)",
            (_WITH_OBJECTIVES, _WITHOUT_OBJECTIVES),
        )
        cur.execute(
            "DELETE FROM chunks WHERE nct_id IN (%s, %s)", (_WITH_OBJECTIVES, _WITHOUT_OBJECTIVES)
        )
        cur.execute(
            "DELETE FROM trials WHERE nct_id IN (%s, %s)", (_WITH_OBJECTIVES, _WITHOUT_OBJECTIVES)
        )
    connection.commit()
    connection.close()


@pytest.fixture
def fixture_corpus(conn: psycopg.Connection) -> psycopg.Connection:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trials (nct_id, brief_title) VALUES (%s, 'With Objectives'), "
            "(%s, 'Without Objectives')",
            (_WITH_OBJECTIVES, _WITHOUT_OBJECTIVES),
        )
        cur.execute(
            "INSERT INTO chunks (chunk_id, nct_id, doc_type, doc_version, section, chunk_type, "
            "is_ocr, text, embedding, embedding_cache_key) VALUES "
            "(%s, %s, 'protocol', 1, 'objectives', 'text', FALSE, %s, %s, 'k1')",
            (
                _OBJECTIVES_CHUNK_ID,
                _WITH_OBJECTIVES,
                _OBJECTIVES_CHUNK_TEXT,
                Vector(_OBJECTIVES_EMBEDDING),
            ),
        )
        # _WITHOUT_OBJECTIVES has a protocol chunk, but not in the
        # 'objectives' section -- the prefilter should exclude it entirely,
        # not just rank it low.
        cur.execute(
            "INSERT INTO chunks (chunk_id, nct_id, doc_type, doc_version, section, chunk_type, "
            "is_ocr, text, embedding, embedding_cache_key) VALUES "
            "(%s, %s, 'protocol', 1, 'eligibility', 'text', FALSE, %s, %s, 'k2')",
            (
                f"{_WITHOUT_OBJECTIVES}:protocol:0",
                _WITHOUT_OBJECTIVES,
                _OTHER_SECTION_CHUNK_TEXT,
                Vector(_OTHER_EMBEDDING),
            ),
        )
        cur.execute(
            "INSERT INTO outcomes (nct_id, kind, source, measure) VALUES "
            "(%s, 'PRIMARY', 'registered_first', %s), (%s, 'PRIMARY', 'registered_current', %s), "
            "(%s, 'PRIMARY', 'registered_first', %s), (%s, 'PRIMARY', 'registered_current', %s)",
            (
                _WITH_OBJECTIVES,
                _FIRST_MEASURE,
                _WITH_OBJECTIVES,
                _CURRENT_MEASURE,
                _WITHOUT_OBJECTIVES,
                _FIRST_MEASURE,
                _WITHOUT_OBJECTIVES,
                _CURRENT_MEASURE,
            ),
        )
    conn.commit()
    return conn


def test_decompose_cross_source_query_shape() -> None:
    subqueries = decompose_cross_source_query("Does the protocol match the registry?", "NCT01")

    assert [sq.leg for sq in subqueries] == ["protocol", "registered_first", "registered_current"]
    protocol, first, current = subqueries
    assert protocol.query_text == "Does the protocol match the registry?"
    assert protocol.filters == QueryFilters(
        nct_id="NCT01", doc_type="protocol", section="objectives"
    )
    assert first.query_text is None
    assert first.filters is None
    assert current.query_text is None
    assert current.filters is None


@pytest.mark.db
def test_answer_cross_source_query_retrieves_objectives_chunk(
    fixture_corpus: psycopg.Connection,
) -> None:
    conn = fixture_corpus
    store = TraceStore(conn)
    query_id = store.log_query("What is the primary objective?")

    result = answer_cross_source_query(
        "What is the primary objective?",
        _WITH_OBJECTIVES,
        _FakeEmbedder(),
        conn,
        _RewardsObjectivesChunkReranker(),
        store,
        query_id,
    )

    assert result.protocol_chunk_id == _OBJECTIVES_CHUNK_ID
    assert result.protocol_leg == _OBJECTIVES_CHUNK_TEXT
    assert result.registered_first == _FIRST_MEASURE
    assert result.registered_current == _CURRENT_MEASURE
    assert result.registered_first_outcome_id is not None
    assert result.registered_current_outcome_id is not None


@pytest.mark.db
def test_answer_cross_source_query_no_objectives_section_is_none_not_raise(
    fixture_corpus: psycopg.Connection,
) -> None:
    conn = fixture_corpus
    store = TraceStore(conn)
    query_id = store.log_query("What is the primary objective?")

    result = answer_cross_source_query(
        "What is the primary objective?",
        _WITHOUT_OBJECTIVES,
        _FakeEmbedder(),
        conn,
        _RewardsObjectivesChunkReranker(),
        store,
        query_id,
    )

    assert result.protocol_chunk_id is None
    assert result.protocol_leg is None
    # The registry-side legs are unaffected by the protocol-side retrieval
    # failure -- distinct sources, distinct failure modes.
    assert result.registered_first == _FIRST_MEASURE
    assert result.registered_current == _CURRENT_MEASURE


@pytest.mark.db
def test_answer_cross_source_query_traces_structured_lookups(
    fixture_corpus: psycopg.Connection,
) -> None:
    conn = fixture_corpus
    store = TraceStore(conn)
    before = max_ids(conn)
    query_id = store.log_query("What is the primary objective?")

    answer_cross_source_query(
        "What is the primary objective?",
        _WITH_OBJECTIVES,
        _FakeEmbedder(),
        conn,
        _RewardsObjectivesChunkReranker(),
        store,
        query_id,
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT stage FROM retrieval_step WHERE query_id = %s AND id > %s ORDER BY id",
            (query_id, before["retrieval_step"]),
        )
        stages = [row[0] for row in cur.fetchall()]
    assert stages == [
        "prefilter",
        "dense",
        "bm25",
        "rerank",
        "structured_lookup",
        "structured_lookup",
    ]


@pytest.mark.db
def test_answer_cross_source_query_missing_registry_outcome_is_none(
    conn: psycopg.Connection,
) -> None:
    # A trial with no outcomes rows at all (e.g. the history endpoint never
    # returned a version 0) -- the registry legs must come back None, not
    # raise or return an empty string.
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trials (nct_id, brief_title) VALUES (%s, 'No Outcomes')",
            (_WITH_OBJECTIVES,),
        )
    conn.commit()
    store = TraceStore(conn)
    query_id = store.log_query("What is the primary objective?")

    result = answer_cross_source_query(
        "What is the primary objective?",
        _WITH_OBJECTIVES,
        _FakeEmbedder(),
        conn,
        _RewardsObjectivesChunkReranker(),
        store,
        query_id,
    )

    assert result.registered_first is None
    assert result.registered_first_outcome_id is None
    assert result.registered_current is None
    assert result.registered_current_outcome_id is None
