"""Outcome normalization tests -- S4-03 spec item 9. The deterministic
paths (normalize_timeframe, compare_outcomes' identical-text and
timeframe-reword shortcuts) are pure/fast. Judge-dependent paths need a
real trace store (caching is a real Postgres lookup) and are marked `db`,
same convention as tests/eval/test_correctness_scorer.py -- every Ollama
HTTP call is mocked via `responses`, no live model calls.
"""

from collections.abc import Iterator

import psycopg
import pytest
import responses

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.normalize.outcome import (
    DEFAULT_JUDGE_MODEL_DIGEST,
    PhrasePair,
    _construct_prompt,
    compare_outcomes,
    evaluate_normalization,
    normalize_construct,
    normalize_outcome,
    normalize_timeframe,
)
from protocol_drift.trace.store import TraceStore, compute_prompt_hash

# --- normalize_timeframe: pure, no DB, no judge ------------------------------


def test_normalize_timeframe_parses_months() -> None:
    assert normalize_timeframe("24 months") == 24.0


def test_normalize_timeframe_years_and_months_agree() -> None:
    # discrepancy_definition.md's canonical example.
    assert normalize_timeframe("24 months") == normalize_timeframe("2 years")


def test_normalize_timeframe_none_for_no_duration() -> None:
    assert normalize_timeframe("Bristol-Myers Squibb") is None


def test_normalize_timeframe_none_for_multiple_distinct_durations() -> None:
    # Compound timeframe descriptions are genuinely ambiguous, not this
    # function's call to collapse into one value.
    assert normalize_timeframe("Assessed at 6 months and again at 12 months") is None


def test_normalize_outcome_builds_canonical_fields() -> None:
    result = normalize_outcome("Overall survival (OS)", "24 months", "Intent-to-treat population")
    assert result.construct == "overall survival os"
    assert result.population == "intenttotreat population"
    assert result.timeframe == 24.0
    assert result.raw_text == "Overall survival (OS)"


def test_normalize_outcome_timeframe_falls_back_to_measure_text() -> None:
    result = normalize_outcome("Overall survival at 24 months", timeframe_text=None)
    assert result.timeframe == 24.0


# --- DB fixture (needed even for calls that never hit Ollama, since
# TraceStore/compare_outcomes always take a real connection) ----------------


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


# --- compare_outcomes: deterministic shortcuts, zero judge calls -----------


@pytest.mark.db
@responses.activate
def test_compare_outcomes_identical_text_is_match_with_no_judge_call(
    conn: psycopg.Connection,
) -> None:
    store = TraceStore(conn)
    query_id = store.log_query("normalization test")

    result = compare_outcomes("Overall survival (OS)", "Overall survival (OS)", query_id, store)

    assert result.verdict == "match"
    assert result.method == "identical_text"
    assert len(responses.calls) == 0


@pytest.mark.db
@responses.activate
def test_compare_outcomes_timeframe_reword_is_match_with_no_judge_call(
    conn: psycopg.Connection,
) -> None:
    # Spec item 9's second required case: "24 months" vs. "2 years" scores
    # match via the deterministic timeframe path, zero judge calls.
    store = TraceStore(conn)
    query_id = store.log_query("normalization test")

    result = compare_outcomes(
        "Overall survival (OS) at 24 months", "Overall survival (OS) at 2 years", query_id, store
    )

    assert result.verdict == "match"
    assert result.method == "timeframe_deterministic"
    assert len(responses.calls) == 0


@pytest.mark.db
@responses.activate
def test_compare_outcomes_different_duration_values_falls_through_to_judge(
    conn: psycopg.Connection,
) -> None:
    # Same construct, but a genuinely different duration (12 vs. 24
    # months) is not a unit rewording -- must not take the deterministic
    # shortcut; the judge decides.
    store = TraceStore(conn)
    query_id = store.log_query("normalization test")
    _mock_ollama("VERDICT: ambiguous\nJUSTIFICATION: The timeframe itself changed.")

    result = compare_outcomes(
        "Overall survival (OS) at 12 months", "Overall survival (OS) at 24 months", query_id, store
    )

    assert result.method == "judge"
    assert len(responses.calls) == 2  # /api/tags + /api/generate


# --- compare_outcomes / normalize_construct: judge path --------------------


_CHECKMATE649_A = (
    "Overall survival (OS) of nivolumab + ipilimumab versus oxaliplatin + "
    "fluoropyrimidine in subjects with PD-L1 expressing tumors"
)
_CHECKMATE649_B = (
    "Overall Survival (OS) in Participants Treated With Nivolumab Plus Chemotherapy vs "
    "Chemotherapy With PD-L1 CPS >= 5"
)


def _evict_cached_generation(conn: psycopg.Connection, prompt: str) -> None:
    """This exact (verbatim NCT02872116) text pair is also
    `scripts/build_phrase_pairs.py`'s "checkmate649" fixture, run for real
    (unmocked) by `scripts/run_normalization_eval.py` against this same
    dev database -- so a prior real run can leave a cached judge response
    that `cached_generate`'s (model_digest, prompt_hash) cache lookup
    would return instead of this test's mocked one. Evicting any row for
    this exact prompt first makes the test deterministic regardless of
    what else has been cached here."""
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


@pytest.mark.db
@responses.activate
def test_compare_outcomes_checkmate649_example_is_divergence(conn: psycopg.Connection) -> None:
    # Spec item 9's first required case: the confirmed NCT02872116
    # divergence example, verbatim from discrepancy_definition.md SS3.
    _evict_cached_generation(conn, _construct_prompt(_CHECKMATE649_A, _CHECKMATE649_B))
    store = TraceStore(conn)
    query_id = store.log_query("normalization test")
    _mock_ollama(
        "VERDICT: divergence\n"
        "JUSTIFICATION: The comparator arm and PD-L1 population threshold both changed."
    )

    result = compare_outcomes(_CHECKMATE649_A, _CHECKMATE649_B, query_id, store)

    assert result.verdict == "divergence"
    assert result.method == "judge"


@pytest.mark.db
@responses.activate
def test_normalize_construct_retries_once_on_unparseable_then_defaults_ambiguous(
    conn: psycopg.Connection,
) -> None:
    store = TraceStore(conn)
    query_id = store.log_query("normalization test")
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
        json={"response": "These look kind of similar.", "prompt_eval_count": 5, "eval_count": 5},
    )
    responses.add(
        responses.POST,
        "http://localhost:11434/api/generate",
        json={"response": "Still no clean verdict here.", "prompt_eval_count": 5, "eval_count": 5},
    )

    verdict, _ = normalize_construct(
        "Overall survival", "Progression-free survival", query_id, store
    )

    # Never silently "match" (would hide a normalizer failure) and never
    # "divergence" (an unresolved case is a candidate for human review,
    # not an accusation) -- defaults to ambiguous.
    assert verdict == "ambiguous"
    assert len(responses.calls) == 4  # first call's tags+generate, retry's tags+generate


# --- evaluate_normalization: confusion matrix + accuracy -------------------


@pytest.mark.db
@responses.activate
def test_evaluate_normalization_accuracy_and_confusion_matrix(conn: psycopg.Connection) -> None:
    store = TraceStore(conn)
    pairs = [
        # match, resolved deterministically (identical text) -- correct.
        PhrasePair("p1", "Overall survival", "Overall survival", "match", "test"),
        # divergence, judge agrees -- correct.
        PhrasePair(
            "p2", "Overall survival", "Progression-free survival", "divergence", "test"
        ),
        # ambiguous gold, judge says divergence -- wrong.
        PhrasePair("p3", "Clinical benefit", "Overall treatment response", "ambiguous", "test"),
    ]
    _mock_ollama("VERDICT: divergence\nJUSTIFICATION: placeholder")  # reused by both judge calls

    report = evaluate_normalization(pairs, store)

    assert report.n == 3
    assert report.accuracy == pytest.approx(2 / 3)
    assert report.confusion["match"]["match"] == 1
    assert report.confusion["divergence"]["divergence"] == 1
    assert report.confusion["ambiguous"]["divergence"] == 1
    assert report.method_counts["identical_text"] == 1
    assert report.method_counts["judge"] == 2
