"""Answer generation tests. build_prompt is pure and runs in the fast path.
generate_answer needs a real trace store (its caching is a real Postgres
lookup) and is marked `db`, same convention as tests/trace/test_store.py.
Every Ollama HTTP call is mocked via `responses` (already used elsewhere in
this codebase, e.g. tests/registry/test_client.py) -- no live model calls.
"""

from collections.abc import Iterator

import psycopg
import pytest
import responses

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.eval.models import EvalQuestion
from protocol_drift.generation.answer import (
    REFUSAL_TOKEN,
    RetrievedChunk,
    build_prompt,
    generate_answer,
)
from protocol_drift.trace.store import TraceStore

QUESTION = EvalQuestion(
    question_id="q1",
    nct_id="NCT00000001",
    question_text="What is the primary outcome?",
    gold_answer="overall survival",
    gold_chunk_ids=["NCT00000001:protocol:0"],
)

CHUNKS = [
    RetrievedChunk(
        chunk_id="NCT00000001:protocol:0",
        text="[NCT00000001 | protocol v1 | outcomes]\nThe primary outcome is overall survival.",
    ),
    RetrievedChunk(
        chunk_id="NCT00000001:protocol:5",
        text="[NCT00000001 | protocol v1 | eligibility]\nPatients must be at least 18 years old.",
    ),
]

_TAGS_RESPONSE = {
    "models": [
        {
            "name": "llama3.1:latest",
            "digest": "46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e",
        }
    ]
}


def test_build_prompt_includes_every_chunk_header() -> None:
    prompt = build_prompt(QUESTION, CHUNKS)

    assert "[NCT00000001 | protocol v1 | outcomes]" in prompt
    assert "[NCT00000001 | protocol v1 | eligibility]" in prompt
    assert QUESTION.question_text in prompt
    assert REFUSAL_TOKEN in prompt
    # numbered so the model can cite by excerpt number, not chunk_id
    assert "[1]" in prompt
    assert "[2]" in prompt


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
    before_ids = _max_ids(connection)
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        for table in _TABLES_CHILD_TO_PARENT:
            cur.execute(f"DELETE FROM {table} WHERE id > %s", (before_ids[table],))
    connection.commit()
    connection.close()


def _mock_ollama(response_text: str) -> None:
    responses.add(responses.GET, "http://localhost:11434/api/tags", json=_TAGS_RESPONSE)
    responses.add(
        responses.POST,
        "http://localhost:11434/api/generate",
        json={
            "response": response_text,
            "prompt_eval_count": 50,
            "eval_count": 10,
            "total_duration": 123_000_000,
        },
    )


@pytest.mark.db
@responses.activate
def test_generate_answer_parses_citations(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    _mock_ollama("The primary outcome is overall survival [1].")

    answer = generate_answer(QUESTION, CHUNKS, store)

    assert answer.is_refusal is False
    assert answer.cited_chunk_ids == ["NCT00000001:protocol:0"]
    assert answer.from_cache is False


@pytest.mark.db
@responses.activate
def test_generate_answer_refusal_round_trips_without_citations(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    _mock_ollama(REFUSAL_TOKEN)

    answer = generate_answer(QUESTION, CHUNKS, store)

    assert answer.is_refusal is True
    assert answer.cited_chunk_ids == []
    assert answer.response_text == REFUSAL_TOKEN


@pytest.mark.db
@responses.activate
def test_generate_answer_caches_on_prompt_hash(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    _mock_ollama("The primary outcome is overall survival [1].")

    first = generate_answer(QUESTION, CHUNKS, store)
    assert len(responses.calls) == 2  # /api/tags + /api/generate

    second = generate_answer(QUESTION, CHUNKS, store)

    assert len(responses.calls) == 2  # no new Ollama calls on the cache hit
    assert second.from_cache is True
    assert second.response_text == first.response_text
    assert second.cited_chunk_ids == first.cited_chunk_ids
    assert second.query_id != first.query_id  # still a distinct traced query
    assert second.generation_id != first.generation_id  # still its own generation row

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM query")
        row = cur.fetchone()
        assert row is not None and row[0] == 2
        cur.execute("SELECT count(*) FROM generation")
        row = cur.fetchone()
        assert row is not None and row[0] == 2
