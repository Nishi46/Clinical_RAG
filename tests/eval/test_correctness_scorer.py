"""Correctness + faithfulness scorer tests. exact_match_score is pure and
runs in the fast path. The judge-scoring paths need a real trace store
(caching is a real Postgres lookup) and are marked `db`, same convention as
tests/generation/test_answer.py. Every Ollama HTTP call is mocked via
`responses` -- no live model calls.
"""

from collections.abc import Iterator

import psycopg
import pytest
import responses

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.eval.correctness_scorer import (
    claim_grounded,
    exact_match_score,
    extract_claims,
    faithfulness_score,
    judged_correctness,
)
from protocol_drift.eval.models import EvalQuestion
from protocol_drift.generation.answer import RetrievedChunk
from protocol_drift.trace.store import TraceStore

QUESTION = EvalQuestion(
    question_id="q1",
    nct_id="NCT00000001",
    question_text="What is the primary outcome timeframe?",
    gold_answer="24 months",
    gold_chunk_ids=["NCT00000001:protocol:0"],
)


def test_exact_match_score_handles_unit_format_variant() -> None:
    # discrepancy_definition.md's canonical example: a timeframe difference
    # attributable only to unit/format is a match, not a divergence.
    assert exact_match_score("The primary outcome timeframe is 2 years.", "24 months") is True


def test_exact_match_score_rejects_different_duration() -> None:
    assert exact_match_score("The timeframe is 18 months.", "24 months") is False


def test_exact_match_score_plain_value_in_sentence() -> None:
    assert exact_match_score("The sponsor is Bristol-Myers Squibb.", "Bristol-Myers Squibb") is True


def test_exact_match_score_rejects_short_number_inside_longer_number() -> None:
    assert exact_match_score("Enrollment target is 16 subjects.", "6") is False


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
    responses.add(
        responses.GET,
        "http://localhost:11434/api/tags",
        json={
            "models": [
                {
                    "name": "llama3.1:latest",
                    "digest": "46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e",
                }
            ]
        },
    )
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
def test_judged_correctness_parses_canned_score(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query(QUESTION.question_text)
    _mock_ollama("SCORE: 1\nJUSTIFICATION: The answer matches the reference exactly.")

    score, justification = judged_correctness(
        QUESTION, "The timeframe is 24 months.", "gold notes: 24 months", query_id, store
    )

    assert score == 1.0
    assert "matches the reference" in justification


@pytest.mark.db
@responses.activate
def test_judged_correctness_retries_once_on_unparseable_then_gives_up(
    conn: psycopg.Connection,
) -> None:
    store = TraceStore(conn)
    query_id = store.log_query(QUESTION.question_text)
    responses.add(
        responses.GET,
        "http://localhost:11434/api/tags",
        json={
            "models": [
                {
                    "name": "llama3.1:latest",
                    "digest": "46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e",
                }
            ]
        },
    )
    responses.add(
        responses.POST,
        "http://localhost:11434/api/generate",
        json={"response": "I think it's pretty good.", "prompt_eval_count": 5, "eval_count": 5},
    )
    responses.add(
        responses.POST,
        "http://localhost:11434/api/generate",
        json={"response": "Still no clear score here.", "prompt_eval_count": 5, "eval_count": 5},
    )

    score, _ = judged_correctness(QUESTION, "some answer", "some notes", query_id, store)

    assert score is None
    # first call's /api/tags + generate, second retry's /api/tags + generate
    assert len(responses.calls) == 4


@pytest.mark.db
@responses.activate
def test_extract_claims_splits_lines(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query(QUESTION.question_text)
    _mock_ollama("The primary outcome is overall survival.\nThe timeframe is 24 months.")

    claims = extract_claims(
        "The primary outcome is overall survival at 24 months.", query_id, store
    )

    assert claims == [
        "The primary outcome is overall survival.",
        "The timeframe is 24 months.",
    ]


@pytest.mark.db
@responses.activate
def test_claim_grounded_true_for_yes(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query(QUESTION.question_text)
    _mock_ollama("YES")
    chunks = [RetrievedChunk(chunk_id="c1", text="The primary outcome is overall survival.")]

    assert (
        claim_grounded("The primary outcome is overall survival.", chunks, query_id, store) is True
    )


@pytest.mark.db
@responses.activate
def test_claim_grounded_false_for_no(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query(QUESTION.question_text)
    _mock_ollama("NO")
    chunks = [RetrievedChunk(chunk_id="c1", text="Unrelated text.")]

    assert (
        claim_grounded("The primary outcome is overall survival.", chunks, query_id, store) is False
    )


@pytest.mark.db
@responses.activate
def test_faithfulness_score_ratio_of_grounded_claims(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query(QUESTION.question_text)
    chunks = [RetrievedChunk(chunk_id="c1", text="The primary outcome is overall survival.")]

    responses.add(
        responses.GET,
        "http://localhost:11434/api/tags",
        json={
            "models": [
                {
                    "name": "llama3.1:latest",
                    "digest": "46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e",
                }
            ]
        },
    )
    # 1st call: extract_claims -> two claims. 2nd/3rd calls: claim_grounded -> YES, NO.
    responses.add(
        responses.POST,
        "http://localhost:11434/api/generate",
        json={"response": "The primary outcome is overall survival.\nThe drug is free.\n"},
    )
    responses.add(responses.POST, "http://localhost:11434/api/generate", json={"response": "YES"})
    responses.add(responses.POST, "http://localhost:11434/api/generate", json={"response": "NO"})

    result = faithfulness_score(
        "The primary outcome is overall survival. The drug is free.", chunks, query_id, store
    )

    assert result.claims == ["The primary outcome is overall survival.", "The drug is free."]
    assert result.grounded == [True, False]
    assert result.score == pytest.approx(0.5)


@pytest.mark.db
def test_faithfulness_score_no_claims_is_zero_not_excluded(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query(QUESTION.question_text)
    chunks = [RetrievedChunk(chunk_id="c1", text="irrelevant")]

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "http://localhost:11434/api/tags",
            json={
                "models": [
                    {
                        "name": "llama3.1:latest",
                        "digest": (
                            "46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e"
                        ),
                    }
                ]
            },
        )
        rsps.add(responses.POST, "http://localhost:11434/api/generate", json={"response": ""})

        result = faithfulness_score("NOT_ANSWERABLE", chunks, query_id, store)

    assert result.claims == []
    assert result.score == 0.0
