"""render_ablation_md tests -- verifies the SQL latency aggregation against
a small fixture of hand-inserted trace-DB rows, not a live model run (per
the S3-12 spec). Marked `db`, same convention as the rest of this project's
trace-store tests.
"""

from collections.abc import Iterator

import psycopg
import pytest

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.eval.ablation import AblationReport, RungResult, render_ablation_md
from protocol_drift.eval.retrieval_scorer import RetrievalScores
from protocol_drift.trace.store import TraceStore

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


def _make_rung_result(conn: psycopg.Connection) -> RungResult:
    store = TraceStore(conn)
    query_id = store.log_query("fixture question", tier="T1")

    # Hand-worked latency fixtures: percentile_cont(0.5)/(0.95) with linear
    # interpolation over a 0-indexed rank of P*(N-1).
    #   dense: [10, 20, 30, 40, 50] -> p50 = v[2] = 30.0 exactly (rank=2.0)
    #                                  p95: rank=0.95*4=3.8 -> 40+0.8*10=48.0
    #                                  mean = 30.0
    #   bm25:  [100, 200] -> p50: rank=0.5*1=0.5 -> 100+0.5*100=150.0
    #                        p95: rank=0.95*1=0.95 -> 100+0.95*100=195.0
    #                        mean = 150.0
    for latency in (10.0, 20.0, 30.0, 40.0, 50.0):
        store.log_retrieval_step(query_id, "dense", latency)
    for latency in (100.0, 200.0):
        store.log_retrieval_step(query_id, "bm25", latency)

    retrieval = RetrievalScores(
        n_questions=10,
        recall_at_k={1: 0.5, 5: 0.6, 10: 0.7, 20: 0.8},
        precision_at_k={1: 0.1, 5: 0.2, 10: 0.3, 20: 0.4},
        mrr=0.55,
        ndcg_at_10=0.65,
    )
    return RungResult(
        name="fixture-rung",
        retrieval=retrieval,
        correctness_t1=0.9,
        correctness_t2=0.4,
        faithfulness=0.75,
        query_id_min=query_id,
        query_id_max=query_id,
    )


@pytest.mark.db
def test_render_ablation_md_includes_retrieval_quality_table(conn: psycopg.Connection) -> None:
    report = AblationReport(rungs=[_make_rung_result(conn)])

    rendered = render_ablation_md(report, conn)

    assert "| fixture-rung | 0.500 | 0.600 | 0.700 | 0.800 | 0.550 | 0.650 |" in rendered
    assert "| fixture-rung | 0.100 | 0.200 | 0.300 | 0.400 |" in rendered


@pytest.mark.db
def test_render_ablation_md_includes_generation_quality_table(conn: psycopg.Connection) -> None:
    report = AblationReport(rungs=[_make_rung_result(conn)])

    rendered = render_ablation_md(report, conn)

    assert "| fixture-rung | 0.900 | 0.400 | 0.750 |" in rendered


@pytest.mark.db
def test_render_ablation_md_latency_matches_hand_computed_percentiles(
    conn: psycopg.Connection,
) -> None:
    report = AblationReport(rungs=[_make_rung_result(conn)])

    rendered = render_ablation_md(report, conn)

    assert "| fixture-rung | dense | 5 | 30.0 | 48.0 | 30.0 |" in rendered
    assert "| fixture-rung | bm25 | 2 | 150.0 | 195.0 | 150.0 |" in rendered


@pytest.mark.db
def test_render_ablation_md_handles_none_generation_scores(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    query_id = store.log_query("fixture question 2", tier="T1")
    store.log_retrieval_step(query_id, "rrf", 5.0)

    retrieval = RetrievalScores(
        n_questions=1,
        recall_at_k={1: 1.0, 5: 1.0, 10: 1.0, 20: 1.0},
        precision_at_k={1: 1.0, 5: 0.2, 10: 0.1, 20: 0.05},
        mrr=1.0,
        ndcg_at_10=1.0,
    )
    rung = RungResult(
        name="retrieval-only-rung",
        retrieval=retrieval,
        correctness_t1=None,
        correctness_t2=None,
        faithfulness=None,
        query_id_min=query_id,
        query_id_max=query_id,
    )
    report = AblationReport(rungs=[rung])

    rendered = render_ablation_md(report, conn)

    assert "| retrieval-only-rung | n/a | n/a | n/a |" in rendered
