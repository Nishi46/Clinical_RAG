"""Retrieval scorer tests. The plain metric functions (recall_at_k,
precision_at_k, mrr, ndcg_at_k) are pure and run in the fast path. The
score_retrieval_run test needs a real Postgres trace store (S1-08) and is
marked `db`, same convention as tests/trace/test_store.py.
"""

import math
from collections.abc import Iterator

import psycopg
import pytest

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.eval.models import EvalQuestion
from protocol_drift.eval.retrieval_scorer import (
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    score_retrieval_run,
)
from protocol_drift.trace.store import TraceStore

# Retrieved 5, gold = {"A", "B"} at ranks 2 and 4 (1-indexed).
RETRIEVED = ["X", "A", "Y", "B", "Z"]
GOLD = ["A", "B"]


def test_recall_at_1_and_5() -> None:
    assert recall_at_k(RETRIEVED, GOLD, k=1) == 0.0  # top-1 is "X", no gold hit
    assert recall_at_k(RETRIEVED, GOLD, k=5) == 1.0  # both gold chunks found by rank 5


def test_recall_at_k_empty_gold_is_zero() -> None:
    assert recall_at_k(RETRIEVED, [], k=5) == 0.0


def test_precision_at_k() -> None:
    assert precision_at_k(RETRIEVED, GOLD, k=1) == 0.0  # 0/1
    assert precision_at_k(RETRIEVED, GOLD, k=5) == pytest.approx(2 / 5)


def test_mrr_reciprocal_of_first_hit_rank() -> None:
    # First gold hit ("A") is at rank 2 -> RR = 1/2.
    assert mrr(RETRIEVED, GOLD) == pytest.approx(0.5)


def test_mrr_no_hit_is_zero() -> None:
    assert mrr(["X", "Y", "Z"], GOLD) == 0.0


def test_ndcg_at_k_matches_worked_example() -> None:
    # Hand-worked against the log2 discount formula directly (not the
    # implementation): relevances at ranks 1..5 are [0,1,0,1,0].
    expected_dcg = 1.0 / math.log2(3) + 1.0 / math.log2(5)  # hits at rank 2 and 4
    expected_idcg = 1.0 / math.log2(2) + 1.0 / math.log2(3)  # ideal: both hits at rank 1, 2
    expected_ndcg = expected_dcg / expected_idcg

    assert ndcg_at_k(RETRIEVED, GOLD, k=10) == pytest.approx(expected_ndcg)


def test_ndcg_at_k_perfect_ranking_is_one() -> None:
    assert ndcg_at_k(["A", "B", "X"], GOLD, k=10) == pytest.approx(1.0)


def test_ndcg_at_k_no_gold_is_zero() -> None:
    assert ndcg_at_k(RETRIEVED, [], k=10) == 0.0


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


@pytest.mark.db
def test_score_retrieval_run_writes_full_trace(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    before = _max_ids(conn)
    questions = [
        EvalQuestion(
            question_id="q1",
            nct_id="NCT00000001",
            question_text="What is the primary outcome?",
            gold_answer="overall survival",
            gold_chunk_ids=["A", "B"],
        ),
        EvalQuestion(
            question_id="q2",
            nct_id="NCT00000002",
            question_text="What is the enrollment target?",
            gold_answer="42",
            gold_chunk_ids=["Z"],
        ),
    ]

    def fake_retrieve(question_text: str, query_id: int) -> list[str]:
        assert query_id > 0
        return RETRIEVED if "primary" in question_text else ["Z", "Q"]

    scores = score_retrieval_run(questions, fake_retrieve, store, stage="dense")

    assert scores.n_questions == 2
    # q1: recall@5=1.0, q2: recall@5=1.0 (both "Z" in top-5) -> mean 1.0
    assert scores.recall_at_k[5] == pytest.approx(1.0)
    # q1 MRR=0.5 (first hit rank 2), q2 MRR=1.0 (hit at rank 1) -> mean 0.75
    assert scores.mrr == pytest.approx(0.75)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM query WHERE id > %s", (before["query"],))
        row = cur.fetchone()
        assert row is not None and row[0] == 2
        cur.execute(
            "SELECT count(*) FROM retrieval_step WHERE stage = 'dense' AND id > %s",
            (before["retrieval_step"],),
        )
        row = cur.fetchone()
        assert row is not None and row[0] == 2
        cur.execute("SELECT count(*) FROM chunk_hit WHERE id > %s", (before["chunk_hit"],))
        row = cur.fetchone()
        assert row is not None and row[0] == 5 + 2  # 5 hits for q1, 2 for q2
