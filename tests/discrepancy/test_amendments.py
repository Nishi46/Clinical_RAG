"""outcome_amendment_events tests against a real Postgres (protocol_drift_dev
locally, or whatever PROTOCOL_DRIFT_DSN points at in CI's service
container). Marked `db`, same convention as tests/eval/test_t1_questions.py:
a fixture trial (NCT99999999) with known amendment rows is inserted,
exercised, then deleted -- never touches the real loaded cohort.
"""

from collections.abc import Iterator

import psycopg
import pytest

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.discrepancy.amendments import outcome_amendment_events

_NCT_ID = "NCT99999999"


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO trials (nct_id, brief_title) VALUES (%s, 'Test Trial')", (_NCT_ID,)
        )
        cur.execute(
            "INSERT INTO amendments (nct_id, version, date, modules_changed) VALUES "
            "(%s, 1, '2020-01-01', %s), (%s, 2, '2020-06-01', %s), (%s, 3, '2021-01-01', %s)",
            (
                _NCT_ID,
                ["Study Status"],
                _NCT_ID,
                ["Outcome Measures", "Eligibility"],
                _NCT_ID,
                ["Outcome Measures"],
            ),
        )
    connection.commit()
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        cur.execute("DELETE FROM amendments WHERE nct_id = %s", (_NCT_ID,))
        cur.execute("DELETE FROM trials WHERE nct_id = %s", (_NCT_ID,))
    connection.commit()
    connection.close()


@pytest.mark.db
def test_outcome_amendment_events_filters_to_outcome_touching_rows(
    conn: psycopg.Connection,
) -> None:
    events = outcome_amendment_events(_NCT_ID, conn)
    assert [e.version for e in events] == [2, 3]  # version 1 didn't touch Outcome Measures


@pytest.mark.db
def test_outcome_amendment_events_ordered_oldest_first(conn: psycopg.Connection) -> None:
    events = outcome_amendment_events(_NCT_ID, conn)
    assert events[0].date == "2020-06-01"
    assert events[1].date == "2021-01-01"


@pytest.mark.db
def test_outcome_amendment_events_empty_for_trial_with_no_amendments(
    conn: psycopg.Connection,
) -> None:
    assert outcome_amendment_events("NCT00000000", conn) == []
