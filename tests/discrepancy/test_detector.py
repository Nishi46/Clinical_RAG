"""Discrepancy detector tests -- S4-05.

`render_verdict_text` is pure/templated (no model call, no DB) and its
denylist guardrail runs in the fast path. `detect_discrepancies` drives a
real trace store + Postgres and is marked `db`; any judge call it makes is
mocked via `responses`, same convention as tests/normalize/test_outcome.py
-- no live model calls.
"""

from collections.abc import Iterator

import psycopg
import pytest
import responses
from pgvector import Vector
from pgvector.psycopg import register_vector

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.discrepancy.detector import (
    PAIR_TYPES,
    PairVerdict,
    detect_discrepancies,
    render_verdict_text,
)
from protocol_drift.normalize.outcome import DEFAULT_JUDGE_MODEL_DIGEST, _construct_prompt
from protocol_drift.trace.store import TraceStore, compute_prompt_hash

_DENYLIST = ("fraud", "lied", "hid", "cheat")


# --- render_verdict_text: pure, no DB, no judge -----------------------------


def test_render_verdict_text_never_contains_denylisted_words() -> None:
    cases = []
    for pair in PAIR_TYPES:
        for verdict in ("match", "divergence", "ambiguous"):
            cases.append(
                PairVerdict(pair=pair, verdict=verdict, retrieval_failed=False, method="judge")
            )
        cases.append(PairVerdict(pair=pair, verdict=None, retrieval_failed=True, method=None))

    for case in cases:
        text = render_verdict_text(case).lower()
        for word in _DENYLIST:
            assert word not in text, f"denylisted word {word!r} found in: {text!r}"


def test_render_verdict_text_retrieval_failed_distinct_from_ambiguous() -> None:
    retrieval_failed = PairVerdict(
        pair="current_vs_protocol", verdict=None, retrieval_failed=True, method=None
    )
    ambiguous = PairVerdict(
        pair="current_vs_protocol", verdict="ambiguous", retrieval_failed=False, method="judge"
    )
    assert render_verdict_text(retrieval_failed) != render_verdict_text(ambiguous)
    assert "could not be retrieved" in render_verdict_text(retrieval_failed)
    assert "flagged for human review" in render_verdict_text(ambiguous)


# --- detect_discrepancies: real Postgres + trace store ----------------------

_CHECKMATE649 = "NCT02872116"
_IDENTICAL_TRIAL = "NCT99999903"

# Verbatim from discrepancy_definition.md SS3 -- the confirmed real
# divergence example. The archived version-0/current snapshot files this
# fixture would ideally come from (tests/fixtures/NCT02872116_*.json, per
# sprint_4_implementation.md) don't exist in this repo, so the two outcome
# texts are inserted directly as this test's registry-only fixture -- the
# spec's own step 7 says this pair "needs no chunk fixtures."
_CHECKMATE649_FIRST = (
    "Overall survival (OS) of nivolumab + ipilimumab versus oxaliplatin + "
    "fluoropyrimidine in subjects with PD-L1 expressing tumors"
)
_CHECKMATE649_CURRENT = (
    "Overall Survival (OS) in Participants Treated With Nivolumab Plus Chemotherapy vs "
    "Chemotherapy With PD-L1 CPS >= 5"
)

_IDENTICAL_TEXT = "Overall survival (OS), assessed from randomization to death from any cause."


def _evict_cached_generation(conn: psycopg.Connection, prompt: str) -> None:
    """This exact NCT02872116 text pair is also used elsewhere (S4-03's
    phrase-pair set, tests/normalize/test_outcome.py) against this same
    dev database -- see that test's identical helper for why a prior real
    run's cached judge response must be evicted before this test's mock
    can be trusted to govern the result."""
    prompt_hash = compute_prompt_hash(DEFAULT_JUDGE_MODEL_DIGEST, prompt)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM cost_record WHERE generation_id IN "
            "(SELECT id FROM generation WHERE model_digest = %s AND prompt_hash = %s)",
            (DEFAULT_JUDGE_MODEL_DIGEST, prompt_hash),
        )
        cur.execute(
            "DELETE FROM generation WHERE model_digest = %s AND prompt_hash = %s",
            (DEFAULT_JUDGE_MODEL_DIGEST, prompt_hash),
        )
    conn.commit()


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


class _ZeroVectorEmbedder:
    """dense_search's SQL always prefilters to this trial's own nct_id, so
    a trial with at most one candidate chunk never needs a real embedding
    to surface it -- a constant zero vector is enough."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 768 for _ in texts]


class _ConstantScoreReranker:
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [0.0 for _ in pairs]


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    register_vector(connection)
    before: dict[str, int] = {}
    with connection.cursor() as cur:
        for table in ("cost_record", "generation", "chunk_hit", "retrieval_step", "query"):
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
            row = cur.fetchone()
            before[table] = row[0] if row else 0
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        for table in ("cost_record", "generation", "chunk_hit", "retrieval_step", "query"):
            cur.execute(f"DELETE FROM {table} WHERE id > %s", (before[table],))
        cur.execute(
            "DELETE FROM outcomes WHERE nct_id IN (%s, %s)", (_CHECKMATE649, _IDENTICAL_TRIAL)
        )
        cur.execute(
            "DELETE FROM chunks WHERE nct_id IN (%s, %s)", (_CHECKMATE649, _IDENTICAL_TRIAL)
        )
        cur.execute(
            "DELETE FROM trials WHERE nct_id IN (%s, %s)", (_CHECKMATE649, _IDENTICAL_TRIAL)
        )
    connection.commit()
    connection.close()


@pytest.mark.db
@responses.activate
def test_checkmate649_registry_only_pair_is_divergence(conn: psycopg.Connection) -> None:
    _evict_cached_generation(conn, _construct_prompt(_CHECKMATE649_FIRST, _CHECKMATE649_CURRENT))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trials (nct_id, brief_title) VALUES (%s, 'CheckMate-649')",
            (_CHECKMATE649,),
        )
        cur.execute(
            "INSERT INTO outcomes (nct_id, kind, source, measure) VALUES "
            "(%s, 'PRIMARY', 'registered_first', %s), (%s, 'PRIMARY', 'registered_current', %s)",
            (_CHECKMATE649, _CHECKMATE649_FIRST, _CHECKMATE649, _CHECKMATE649_CURRENT),
        )
    conn.commit()
    _mock_ollama(
        "VERDICT: divergence\n"
        "JUSTIFICATION: The comparator arm and PD-L1 population threshold both changed."
    )
    store = TraceStore(conn)
    query_id = store.log_query("What is the primary outcome?")

    report = detect_discrepancies(
        _CHECKMATE649, conn, _ZeroVectorEmbedder(), _ConstantScoreReranker(), store, query_id
    )

    verdict = report.pairs["first_posted_vs_current"]
    assert verdict is not None
    assert verdict.verdict == "divergence"
    assert verdict.retrieval_failed is False
    assert verdict.citations["registered_first_outcome_id"] is not None
    assert verdict.citations["registered_current_outcome_id"] is not None

    # No protocol chunk fixture exists for this trial -- current_vs_protocol
    # must come back a graded retrieval_failed verdict, never silently
    # dropped and never defaulted to divergence.
    protocol_pair = report.pairs["current_vs_protocol"]
    assert protocol_pair is not None
    assert protocol_pair.retrieval_failed is True
    assert protocol_pair.verdict is None


@pytest.mark.db
def test_identical_text_across_all_sources_is_match_on_every_pair(
    conn: psycopg.Connection,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trials (nct_id, brief_title) VALUES (%s, 'Identical Trial')",
            (_IDENTICAL_TRIAL,),
        )
        cur.execute(
            "INSERT INTO chunks (chunk_id, nct_id, doc_type, doc_version, section, chunk_type, "
            "is_ocr, text, embedding, embedding_cache_key) VALUES "
            "(%s, %s, 'protocol', 1, 'objectives', 'text', FALSE, %s, %s, 'k1')",
            (
                f"{_IDENTICAL_TRIAL}:protocol:0",
                _IDENTICAL_TRIAL,
                _IDENTICAL_TEXT,
                Vector([0.0] * 768),
            ),
        )
        cur.execute(
            "INSERT INTO outcomes (nct_id, kind, source, measure) VALUES "
            "(%s, 'PRIMARY', 'registered_first', %s), "
            "(%s, 'PRIMARY', 'registered_current', %s), "
            "(%s, 'PRIMARY', 'results_reported', %s)",
            (
                _IDENTICAL_TRIAL,
                _IDENTICAL_TEXT,
                _IDENTICAL_TRIAL,
                _IDENTICAL_TEXT,
                _IDENTICAL_TRIAL,
                _IDENTICAL_TEXT,
            ),
        )
    conn.commit()
    store = TraceStore(conn)
    query_id = store.log_query("What is the primary outcome?")

    # No @responses.activate -- identical text resolves deterministically
    # (S4-03's "identical_text" shortcut) on every pair, so this test
    # asserts zero judge calls implicitly: an unexpected Ollama call would
    # raise a connection error with no mock registered.
    report = detect_discrepancies(
        _IDENTICAL_TRIAL, conn, _ZeroVectorEmbedder(), _ConstantScoreReranker(), store, query_id
    )

    for pair in PAIR_TYPES:
        verdict = report.pairs[pair]
        assert verdict is not None, f"{pair} unexpectedly not applicable"
        assert verdict.verdict == "match", f"{pair} was {verdict.verdict}, not match"
        assert verdict.method == "identical_text"


@pytest.mark.db
def test_discrepancy_report_to_dict_matches_scorer_loader_shape(
    conn: psycopg.Connection,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trials (nct_id, brief_title) VALUES (%s, 'Identical Trial')",
            (_IDENTICAL_TRIAL,),
        )
        cur.execute(
            "INSERT INTO outcomes (nct_id, kind, source, measure) VALUES "
            "(%s, 'PRIMARY', 'registered_first', %s), "
            "(%s, 'PRIMARY', 'registered_current', %s)",
            (_IDENTICAL_TRIAL, _IDENTICAL_TEXT, _IDENTICAL_TRIAL, _IDENTICAL_TEXT),
        )
    conn.commit()
    store = TraceStore(conn)
    query_id = store.log_query("What is the primary outcome?")

    report = detect_discrepancies(
        _IDENTICAL_TRIAL, conn, _ZeroVectorEmbedder(), _ConstantScoreReranker(), store, query_id
    )
    payload = report.to_dict()

    assert payload["nct_id"] == _IDENTICAL_TRIAL
    assert payload["pairs"]["first_posted_vs_current"]["verdict"] == "match"
    assert payload["pairs"]["first_posted_vs_current"]["retrieval_failed"] is False
    # No protocol chunk / no results_reported row for this fixture --
    # current_vs_protocol comes back a graded retrieval_failed verdict
    # (not None: registered_current exists), registry_vs_results comes
    # back None (not applicable: no results reported at all).
    assert payload["pairs"]["current_vs_protocol"]["retrieval_failed"] is True
    assert payload["pairs"]["registry_vs_results"] is None
