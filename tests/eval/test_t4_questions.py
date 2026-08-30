"""T4 question generation tests against a real Postgres (protocol_drift_dev
locally, or whatever PROTOCOL_DRIFT_DSN points at in CI's service
container). Marked `db`, same convention as tests/eval/test_t1_questions.py:
fixture trials (NCT999999xx, clearly not real cohort trials) with known
amendments/outcomes rows are inserted, exercised, then deleted -- never
touches the real loaded cohort.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.eval.t4_questions import build_t4_dataset, generate_t4_questions

_ONE_REVISION = "NCT99999901"  # exactly one outcome-touching revision
_ZERO_REVISIONS = "NCT99999902"  # never revised

_FIRST_MEASURE = "Overall survival (OS)"
_CURRENT_MEASURE = "Overall survival (OS), CPS >= 5"
_UNCHANGED_MEASURE = "Progression-free survival (PFS)"


def _insert_trial(cur: psycopg.Cursor, nct_id: str) -> None:
    cur.execute("INSERT INTO trials (nct_id, brief_title) VALUES (%s, 'Test Trial')", (nct_id,))


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    with connection.cursor() as cur:
        _insert_trial(cur, _ONE_REVISION)
        cur.execute(
            "INSERT INTO amendments (nct_id, version, date, modules_changed) VALUES (%s, 5, "
            "'2019-05-01', %s)",
            (_ONE_REVISION, ["Outcome Measures"]),
        )
        cur.execute(
            "INSERT INTO outcomes (nct_id, kind, source, measure) VALUES "
            "(%s, 'PRIMARY', 'registered_first', %s), (%s, 'PRIMARY', 'registered_current', %s)",
            (_ONE_REVISION, _FIRST_MEASURE, _ONE_REVISION, _CURRENT_MEASURE),
        )

        _insert_trial(cur, _ZERO_REVISIONS)
        cur.execute(
            "INSERT INTO outcomes (nct_id, kind, source, measure) VALUES "
            "(%s, 'PRIMARY', 'registered_first', %s), (%s, 'PRIMARY', 'registered_current', %s)",
            (_ZERO_REVISIONS, _UNCHANGED_MEASURE, _ZERO_REVISIONS, _UNCHANGED_MEASURE),
        )
    connection.commit()
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        for nct_id in (_ONE_REVISION, _ZERO_REVISIONS):
            cur.execute("DELETE FROM outcomes WHERE nct_id = %s", (nct_id,))
            cur.execute("DELETE FROM amendments WHERE nct_id = %s", (nct_id,))
            cur.execute("DELETE FROM trials WHERE nct_id = %s", (nct_id,))
    connection.commit()
    connection.close()


@pytest.mark.db
def test_single_revision_trial_produces_from_x_to_y_at_version_n(
    conn: psycopg.Connection,
) -> None:
    cohort = {"trials": [{"nct_id": _ONE_REVISION}]}
    questions = generate_t4_questions(cohort, conn)
    by_template = {q.template_id: q for q in questions}

    changed = by_template["changed_when_what"]
    assert changed.question_text == (
        f"Was the primary outcome changed after first posting for {_ONE_REVISION}? "
        "When, and to what?"
    )
    assert changed.gold_answer == (
        f"Yes, the primary outcome was changed, from '{_FIRST_MEASURE}' to '{_CURRENT_MEASURE}', "
        "at version 5 (2019-05-01)."
    )
    assert changed.gold_chunk_ids == []


@pytest.mark.db
def test_single_revision_trial_revision_count_and_first_registered(
    conn: psycopg.Connection,
) -> None:
    cohort = {"trials": [{"nct_id": _ONE_REVISION}]}
    questions = generate_t4_questions(cohort, conn)
    by_template = {q.template_id: q for q in questions}

    assert by_template["revision_count"].gold_answer == "1"
    assert by_template["first_registered"].gold_answer == _FIRST_MEASURE


@pytest.mark.db
def test_zero_revision_trial_produces_unchanged_answer(conn: psycopg.Connection) -> None:
    cohort = {"trials": [{"nct_id": _ZERO_REVISIONS}]}
    questions = generate_t4_questions(cohort, conn)
    by_template = {q.template_id: q for q in questions}

    assert (
        by_template["changed_when_what"].gold_answer
        == "No, the primary outcome was not changed after first posting."
    )
    assert by_template["revision_count"].gold_answer == "0"
    assert by_template["first_registered"].gold_answer == _UNCHANGED_MEASURE


@pytest.mark.db
def test_build_t4_dataset_keeps_unchanged_trial_questions_even_under_a_tight_cap(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    cohort = {"trials": [{"nct_id": _ONE_REVISION}, {"nct_id": _ZERO_REVISIONS}]}
    output_path = tmp_path / "t4.jsonl"

    summary = build_t4_dataset(cohort, conn, target_count=1, output_path=output_path)

    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    assert any(r["nct_id"] == _ZERO_REVISIONS for r in rows), (
        "unchanged-trial questions must survive the cap unconditionally"
    )
    assert summary["unchanged_trial_questions"] == 3
