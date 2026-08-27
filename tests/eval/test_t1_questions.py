"""T1 question generation tests against a real Postgres (protocol_drift_dev
locally, or whatever PROTOCOL_DRIFT_DSN points at in CI's service
container).

Marked `db`, same convention as tests/trace/test_store.py and
tests/retrieval/test_schema.py: a fixture trial (NCT99999999, clearly not a
real cohort trial) with known rows across trials/eligibility/outcomes/arms
and one known chunk is inserted, exercised, then deleted -- never touches
the real loaded corpus or cohort.
"""

from collections.abc import Iterator

import psycopg
import pytest
from pgvector import Vector
from pgvector.psycopg import register_vector

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.eval.t1_questions import generate_t1_questions, locate_gold_chunk

_NCT_ID = "NCT99999999"

_CHUNK_TEXT = (
    "This is a Phase 2 study sponsored by Test Sponsor Inc. "
    "Target enrollment is 42 subjects. "
    "Eligible participants must be 18 Years or older. "
    "Overall survival will be assessed over 24 months. "
    "Test Drug Arm receives the study drug daily."
)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    register_vector(connection)
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO trials (nct_id, brief_title, phase, sponsor_name, enrollment_count) "
            "VALUES (%s, 'Test Trial', 'PHASE2', 'Test Sponsor Inc', 42)",
            (_NCT_ID,),
        )
        cur.execute(
            "INSERT INTO eligibility (nct_id, min_age, max_age) VALUES (%s, '18 Years', NULL)",
            (_NCT_ID,),
        )
        cur.execute(
            "INSERT INTO outcomes (nct_id, kind, source, measure, timeframe) "
            "VALUES (%s, 'PRIMARY', 'registered_current', 'Overall survival', '24 months')",
            (_NCT_ID,),
        )
        cur.execute(
            "INSERT INTO arms (nct_id, arm_label, arm_type) "
            "VALUES (%s, 'Test Drug Arm', 'EXPERIMENTAL')",
            (_NCT_ID,),
        )
        cur.execute(
            "INSERT INTO chunks (chunk_id, nct_id, doc_type, doc_version, section, "
            "chunk_type, is_ocr, text, embedding, embedding_cache_key) "
            "VALUES (%s, %s, 'protocol', 1, 'eligibility', 'text', FALSE, %s, %s, 'cache-key')",
            (f"{_NCT_ID}:protocol:0", _NCT_ID, _CHUNK_TEXT, Vector([0.1] * 768)),
        )
    connection.commit()
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE nct_id = %s", (_NCT_ID,))
        cur.execute("DELETE FROM outcomes WHERE nct_id = %s", (_NCT_ID,))
        cur.execute("DELETE FROM arms WHERE nct_id = %s", (_NCT_ID,))
        cur.execute("DELETE FROM eligibility WHERE nct_id = %s", (_NCT_ID,))
        cur.execute("DELETE FROM trials WHERE nct_id = %s", (_NCT_ID,))
    connection.commit()
    connection.close()


@pytest.mark.db
def test_locate_gold_chunk_finds_known_chunk(conn: psycopg.Connection) -> None:
    matches = locate_gold_chunk(_NCT_ID, "protocol", "42", conn)
    assert matches == [f"{_NCT_ID}:protocol:0"]


@pytest.mark.db
def test_locate_gold_chunk_returns_empty_for_absent_answer(conn: psycopg.Connection) -> None:
    matches = locate_gold_chunk(_NCT_ID, "protocol", "nonexistent fact xyz", conn)
    assert matches == []


@pytest.mark.db
def test_locate_gold_chunk_short_number_does_not_match_inside_longer_number(
    conn: psycopg.Connection,
) -> None:
    # Regression: naive substring search on "4" matched inside "42 subjects"
    # and "24 months" in the fixture chunk -- word-boundary matching must
    # reject both; "4" never appears as its own standalone token here.
    matches = locate_gold_chunk(_NCT_ID, "protocol", "4", conn)
    assert matches == []


@pytest.mark.db
def test_generate_t1_questions_from_known_trial(conn: psycopg.Connection) -> None:
    cohort = {"trials": [{"nct_id": _NCT_ID}]}
    questions = generate_t1_questions(cohort, conn)
    by_template = {q.template_id: q for q in questions}

    assert set(by_template) == {
        "enrollment_count",
        "phase",
        "sponsor",
        "min_age",
        "primary_outcome_measure",
        "primary_outcome_timeframe",
        "arm_label",
    }  # max_age skipped: null in the fixture

    enrollment = by_template["enrollment_count"]
    assert enrollment.question_text == f"What is the target enrollment for {_NCT_ID}?"
    assert enrollment.gold_answer == "42"
    assert enrollment.gold_chunk_ids == [f"{_NCT_ID}:protocol:0"]

    phase = by_template["phase"]
    assert phase.gold_answer == "Phase 2"
    assert phase.gold_chunk_ids == [f"{_NCT_ID}:protocol:0"]

    for q in questions:
        assert q.gold_chunk_ids == [f"{_NCT_ID}:protocol:0"]


@pytest.mark.db
def test_generate_t1_questions_skips_null_fields(conn: psycopg.Connection) -> None:
    cohort = {"trials": [{"nct_id": _NCT_ID}]}
    questions = generate_t1_questions(cohort, conn)
    assert "max_age" not in {q.template_id for q in questions}
