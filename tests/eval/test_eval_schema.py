import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from protocol_drift.eval.models import EvalQuestion

T1_PATH = Path("data/eval/t1.jsonl")
T2_PATH = Path("data/eval/t2.jsonl")


def _load(path: Path) -> list[EvalQuestion]:
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    return [EvalQuestion.model_validate_json(line) for line in lines]


def test_t1_questions_validate_against_shared_model() -> None:
    questions = _load(T1_PATH)
    assert len(questions) >= 150
    for q in questions:
        assert q.template_id is not None
        assert q.gold_chunk_ids


def test_t2_questions_validate_against_shared_model() -> None:
    questions = _load(T2_PATH)
    assert len(questions) >= 80
    for q in questions:
        assert q.gold_answer_notes is not None
        assert q.gold_chunk_ids


def test_t2_spans_multiple_sponsors() -> None:
    questions = _load(T2_PATH)
    assert len({q.nct_id for q in questions}) >= 15


@pytest.mark.db
def test_t2_has_assessment_schedule_targeted_subset() -> None:
    # gold_chunk_ids alone don't carry chunk_type -- cross-reference against
    # the real chunks each question cites to confirm at least one is really
    # an assessment_schedule chunk, not just self-reported.
    import psycopg

    from protocol_drift.db import DEFAULT_DSN

    questions = _load(T2_PATH)
    all_chunk_ids = [cid for q in questions for cid in q.gold_chunk_ids]
    conn = psycopg.connect(DEFAULT_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(DISTINCT chunk_id) FROM chunks "
                "WHERE chunk_id = ANY(%s) AND chunk_type = 'assessment_schedule'",
                (all_chunk_ids,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] >= 15


def test_malformed_row_fails_loudly() -> None:
    malformed = json.dumps({"question_id": "x", "nct_id": "NCT1", "unexpected_field": "oops"})
    with pytest.raises(ValidationError):
        EvalQuestion.model_validate_json(malformed)
