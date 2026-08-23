"""TraceStore tests against a real local Postgres (protocol_drift_dev).

Marked `integration`: unlike db/extract.py's pure extraction functions,
TraceStore has no meaningful behavior without a live database connection.
Adding a Postgres service container to CI so these can run unmarked is a
real, well-scoped follow-up, but not one this pass attempts -- it can't be
verified locally the way everything else here can, and shipping unverified
CI infrastructure is worse than leaving the gap explicit. Run locally:
`pytest -m integration tests/trace/`.
"""

import concurrent.futures
import time
from collections.abc import Iterator

import psycopg
import pytest

from protocol_drift.trace.store import TraceStore, compute_prompt_hash, traced_call

DSN = "dbname=protocol_drift_dev"
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
    # a test that deliberately triggers a constraint violation (e.g. via
    # pytest.raises) leaves the connection's transaction aborted -- clear
    # that before cleanup, or the DELETEs below fail with
    # InFailedSqlTransaction instead of actually cleaning up.
    connection.rollback()
    with connection.cursor() as cur:
        for table in _TABLES_CHILD_TO_PARENT:
            cur.execute(f"DELETE FROM {table} WHERE id > %s", (before_ids[table],))
    connection.commit()
    connection.close()


@pytest.fixture
def store(conn: psycopg.Connection) -> TraceStore:
    return TraceStore(conn)


@pytest.mark.integration
def test_log_query_returns_valid_id(store: TraceStore) -> None:
    query_id = store.log_query("What is the primary outcome?", tier="T1")
    assert query_id > 0


@pytest.mark.integration
def test_log_retrieval_step_returns_valid_id(store: TraceStore) -> None:
    query_id = store.log_query("test query")
    step_id = store.log_retrieval_step(query_id, "dense", latency_ms=12.5)
    assert step_id > 0


@pytest.mark.integration
def test_log_chunk_hit_returns_valid_id(store: TraceStore) -> None:
    query_id = store.log_query("test query")
    step_id = store.log_retrieval_step(query_id, "dense", latency_ms=12.5)
    hit_id = store.log_chunk_hit(
        step_id, chunk_id="chunk-1", rank=1, score=0.9, nct_id="NCT00000001"
    )
    assert hit_id > 0


@pytest.mark.integration
def test_log_generation_returns_valid_id(store: TraceStore) -> None:
    query_id = store.log_query("test query")
    gen_id = store.log_generation(
        query_id,
        model_digest="sha256:abc",
        prompt_hash="deadbeef",
        response_text="answer",
        latency_ms=500.0,
        token_count=42,
    )
    assert gen_id > 0


@pytest.mark.integration
def test_log_cost_returns_valid_id(store: TraceStore) -> None:
    query_id = store.log_query("test query")
    gen_id = store.log_generation(query_id, "sha256:abc", "deadbeef", "answer", 500.0)
    cost_id = store.log_cost(gen_id, tokens_in=100, tokens_out=50, wall_clock_ms=600.0)
    assert cost_id > 0


@pytest.mark.integration
def test_retrieval_step_rejects_unknown_stage(store: TraceStore) -> None:
    query_id = store.log_query("test query")
    with pytest.raises(psycopg.errors.CheckViolation):
        store.log_retrieval_step(query_id, "not_a_real_stage", latency_ms=1.0)


@pytest.mark.integration
def test_chunk_hit_rejects_orphaned_retrieval_step_id(store: TraceStore) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.log_chunk_hit(999_999_999, chunk_id="chunk-1")


@pytest.mark.integration
def test_retrieval_step_rejects_orphaned_query_id(store: TraceStore) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.log_retrieval_step(999_999_999, "dense", latency_ms=1.0)


@pytest.mark.integration
def test_generation_rejects_orphaned_query_id(store: TraceStore) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.log_generation(999_999_999, "sha256:abc", "deadbeef", "answer", 1.0)


@pytest.mark.integration
def test_cost_record_rejects_orphaned_generation_id(store: TraceStore) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.log_cost(999_999_999, tokens_in=1, tokens_out=1, wall_clock_ms=1.0)


@pytest.mark.integration
def test_end_to_end_chain_joins_correctly(store: TraceStore, conn: psycopg.Connection) -> None:
    query_id = store.log_query("full chain test", tier="T2")
    step_id = store.log_retrieval_step(query_id, "rerank", latency_ms=8.0)
    store.log_chunk_hit(
        step_id,
        chunk_id="chunk-42",
        rank=1,
        score=0.95,
        nct_id="NCT00000001",
        doc_type="protocol",
        section="Outcomes",
        page_range="12-13",
    )
    gen_id = store.log_generation(
        query_id,
        "sha256:abc",
        compute_prompt_hash("sha256:abc", "prompt text"),
        "the answer",
        400.0,
        token_count=20,
    )
    store.log_cost(gen_id, tokens_in=80, tokens_out=20, wall_clock_ms=450.0)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT q.text, rs.stage, ch.chunk_id, g.response_text, cr.tokens_in
            FROM query q
            JOIN retrieval_step rs ON rs.query_id = q.id
            JOIN chunk_hit ch ON ch.retrieval_step_id = rs.id
            JOIN generation g ON g.query_id = q.id
            JOIN cost_record cr ON cr.generation_id = g.id
            WHERE q.id = %s
            """,
            (query_id,),
        )
        row = cur.fetchone()

    assert row == ("full chain test", "rerank", "chunk-42", "the answer", 80)


@pytest.mark.integration
def test_traced_call_logs_step_and_hits_and_measures_latency(
    store: TraceStore, conn: psycopg.Connection
) -> None:
    query_id = store.log_query("timed query")

    with traced_call(store, query_id, "dense") as trace:
        time.sleep(0.1)
        trace.chunk_hits = [
            {"chunk_id": "chunk-a", "rank": 1, "score": 0.8},
            {"chunk_id": "chunk-b", "rank": 2, "score": 0.7},
        ]

    with conn.cursor() as cur:
        cur.execute("SELECT stage, latency_ms FROM retrieval_step WHERE query_id = %s", (query_id,))
        stage, latency_ms = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM chunk_hit ch "
            "JOIN retrieval_step rs ON ch.retrieval_step_id = rs.id "
            "WHERE rs.query_id = %s",
            (query_id,),
        )
        (hit_count,) = cur.fetchone()

    assert stage == "dense"
    assert 100 <= latency_ms < 2000  # slept 0.1s; generous upper bound for CI/system jitter
    assert hit_count == 2


@pytest.mark.integration
def test_traced_call_logs_step_even_with_no_hits(
    store: TraceStore, conn: psycopg.Connection
) -> None:
    query_id = store.log_query("empty hits query")

    with traced_call(store, query_id, "bm25"):
        pass

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM retrieval_step WHERE query_id = %s", (query_id,))
        (count,) = cur.fetchone()
    assert count == 1


@pytest.mark.integration
def test_traced_call_logs_step_even_on_exception(
    store: TraceStore, conn: psycopg.Connection
) -> None:
    query_id = store.log_query("exploding query")

    with pytest.raises(ValueError), traced_call(store, query_id, "dense"):
        raise ValueError("boom")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM retrieval_step WHERE query_id = %s", (query_id,))
        (count,) = cur.fetchone()
    assert count == 1


def test_compute_prompt_hash_deterministic() -> None:
    h1 = compute_prompt_hash("sha256:abc", "What is X?")
    h2 = compute_prompt_hash("sha256:abc", "What is X?")
    h3 = compute_prompt_hash("sha256:abc", "What is Y?")

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


@pytest.mark.integration
def test_concurrent_writes_produce_no_lost_writes(conn: psycopg.Connection) -> None:
    """Sprint 3's eval loop will hammer this store from many workers at
    once. Each worker here opens its own connection (a psycopg Connection
    is not safe to share across threads) and writes concurrently; the `conn`
    fixture is used only for baseline/cleanup bookkeeping -- once a worker
    commits, its row is visible to every other connection, so cleanup still
    finds everything regardless of which connection wrote it."""
    n = 20

    def _write(i: int) -> int:
        worker_conn = psycopg.connect(DSN)
        try:
            return TraceStore(worker_conn).log_query(f"concurrent query {i}", tier="T1")
        finally:
            worker_conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(_write, range(n)))

    assert len(ids) == n
    assert len(set(ids)) == n  # every write landed with a distinct id -- none lost or collided

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM query WHERE text LIKE %s", ("concurrent query %",))
        (count,) = cur.fetchone()
    assert count == n
