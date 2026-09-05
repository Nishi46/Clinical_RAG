"""FastAPI serving app tests -- S5-01.

Everything here runs against mocked collaborators (connection, embedder,
reranker, trace store, retrieval/generation functions) via FastAPI's
`dependency_overrides` and `monkeypatch` -- no live Postgres or Ollama, so
these run under plain `pytest` (no `db`/`integration` marker) same as every
other non-DB test suite in this repo.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from protocol_drift.retrieval.types import RetrievedChunk
from protocol_drift.serving import app as app_module


class FakeStore:
    """Stands in for `TraceStore` -- records what it's asked to log without
    touching any connection."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.logged_queries: list[tuple[str, str | None]] = []

    def log_query(self, text: str, tier: str | None = None) -> int:
        self.logged_queries.append((text, tier))
        return 1


def _sse_events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    def fake_get_connection() -> Iterator[object]:
        yield object()

    app_module.app.dependency_overrides[app_module.get_connection] = fake_get_connection
    app_module.app.dependency_overrides[app_module.get_embedder] = lambda: "fake-embedder"
    app_module.app.dependency_overrides[app_module.get_reranker] = lambda: "fake-reranker"
    monkeypatch.setattr(app_module, "TraceStore", FakeStore)
    try:
        yield TestClient(app_module.app)
    finally:
        app_module.app.dependency_overrides.clear()


def test_answer_streams_mocked_response_end_to_end(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "rerank_ladder", lambda *args, **kwargs: ["chunk-1"])
    monkeypatch.setattr(
        app_module,
        "fetch_chunks",
        lambda conn, ids: [RetrievedChunk(chunk_id="chunk-1", text="excerpt text")],
    )

    def fake_stream_answer(
        question: Any, chunks: Any, store: Any, **kwargs: Any
    ) -> Iterator[dict[str, Any]]:
        assert [c.chunk_id for c in chunks] == ["chunk-1"]
        yield {"type": "token", "text": "The answer is "}
        yield {"type": "token", "text": "42 [1]."}
        yield {
            "type": "done",
            "query_id": 1,
            "generation_id": 7,
            "cited_chunk_ids": ["chunk-1"],
            "is_refusal": False,
            "from_cache": False,
        }

    monkeypatch.setattr(app_module, "stream_answer", fake_stream_answer)

    with client.stream(
        "POST",
        "/answer",
        json={"nct_id": "NCT00000001", "question": "What is the primary outcome?"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    events = _sse_events(body)
    assert events[0] == {"type": "token", "text": "The answer is "}
    assert events[1] == {"type": "token", "text": "42 [1]."}
    assert events[-1]["type"] == "done"
    assert events[-1]["cited_chunk_ids"] == ["chunk-1"]


def test_answer_t3_uses_cross_source_query(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_answer_cross_source_query(*args: Any, **kwargs: Any) -> Any:
        class _CrossSource:
            protocol_leg = "protocol text"
            protocol_chunk_id = "chunk-p"
            registered_first = "first text"
            registered_current = "current text"

        return _CrossSource()

    monkeypatch.setattr(
        app_module, "answer_cross_source_query", fake_answer_cross_source_query
    )

    seen_chunk_ids: list[str] = []

    def fake_stream_answer(
        question: Any, chunks: Any, store: Any, **kwargs: Any
    ) -> Iterator[dict[str, Any]]:
        seen_chunk_ids.extend(c.chunk_id for c in chunks)
        yield {
            "type": "done",
            "query_id": 1,
            "generation_id": 1,
            "cited_chunk_ids": [],
            "is_refusal": False,
            "from_cache": False,
        }

    monkeypatch.setattr(app_module, "stream_answer", fake_stream_answer)

    with client.stream(
        "POST",
        "/answer",
        json={"nct_id": "NCT00000001", "question": "Compare the primary outcome.", "tier": "T3"},
    ) as response:
        assert response.status_code == 200
        "".join(response.iter_text())

    assert seen_chunk_ids == [
        "chunk-p",
        "NCT00000001:registered_first",
        "NCT00000001:registered_current",
    ]


class _FakeCursor:
    def __init__(self, execute_error: Exception | None = None) -> None:
        self._execute_error = execute_error

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False

    def execute(self, *args: Any, **kwargs: Any) -> None:
        if self._execute_error is not None:
            raise self._execute_error

    def fetchone(self) -> Any:
        return None


class _FakeConnection:
    def __init__(self, execute_error: Exception | None = None) -> None:
        self._execute_error = execute_error

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._execute_error)

    def close(self) -> None:
        pass


def test_health_ok_when_db_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module.psycopg, "connect", lambda dsn: _FakeConnection())
    client = TestClient(app_module.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_reports_error_when_db_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_connect(dsn: str) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(app_module.psycopg, "connect", raise_connect)
    client = TestClient(app_module.app)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "error"


def test_discrepancy_404s_for_out_of_cohort_nct_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_connection() -> Iterator[_FakeConnection]:
        yield _FakeConnection()

    app_module.app.dependency_overrides[app_module.get_connection] = fake_get_connection
    app_module.app.dependency_overrides[app_module.get_embedder] = lambda: "fake-embedder"
    app_module.app.dependency_overrides[app_module.get_reranker] = lambda: "fake-reranker"
    try:
        client = TestClient(app_module.app)
        response = client.get("/discrepancy/NCT00000000")
    finally:
        app_module.app.dependency_overrides.clear()

    assert response.status_code == 404


def test_discrepancy_returns_report_for_known_trial(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FoundCursor(_FakeCursor):
        def fetchone(self) -> Any:
            return (1,)

    class _FoundConnection(_FakeConnection):
        def cursor(self) -> _FoundCursor:
            return _FoundCursor()

    def fake_get_connection() -> Iterator[_FoundConnection]:
        yield _FoundConnection()

    app_module.app.dependency_overrides[app_module.get_connection] = fake_get_connection

    def fake_detect_discrepancies(*args: Any, **kwargs: Any) -> Any:
        class _Report:
            def to_dict(self) -> dict[str, Any]:
                return {"nct_id": "NCT02872116", "pairs": {}}

        return _Report()

    monkeypatch.setattr(app_module, "detect_discrepancies", fake_detect_discrepancies)

    response = client.get("/discrepancy/NCT02872116")

    assert response.status_code == 200
    assert response.json() == {"nct_id": "NCT02872116", "pairs": {}}
