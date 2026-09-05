"""Adversarial question + refusal metrics tests -- S4-09.
`refusal_metrics` is pure (given a stub `generate_fn`) and runs in the
fast path. `generate_adversarial_questions`'s unposted_document category
depends on real chunk presence in Postgres and is marked `db`.
"""

from collections.abc import Iterator

import psycopg
import pytest

from protocol_drift.db import DEFAULT_DSN as DSN
from protocol_drift.eval.adversarial_questions import (
    AdversarialQuestion,
    generate_adversarial_questions,
    refusal_metrics,
)
from protocol_drift.eval.models import EvalQuestion

_HAS_SAP_TEXT = "NCT99999910"  # has_sap=True and real sap chunks ingested
_NO_SAP_TEXT_BUT_FLAGGED_TRUE = "NCT99999911"  # has_sap=True but zero sap chunks (the real gap)
_HAS_SAP_FALSE = "NCT99999912"  # has_sap=False


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(DSN)
    nct_ids = (_HAS_SAP_TEXT, _NO_SAP_TEXT_BUT_FLAGGED_TRUE, _HAS_SAP_FALSE)
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO trials (nct_id, brief_title, has_sap) VALUES "
            "(%s, 'Has SAP text', TRUE), (%s, 'Flagged true, no SAP text', TRUE), "
            "(%s, 'Has SAP false', FALSE)",
            nct_ids,
        )
        cur.execute(
            "INSERT INTO chunks (chunk_id, nct_id, doc_type, doc_version, section, chunk_type, "
            "is_ocr, text, embedding, embedding_cache_key) VALUES "
            "(%s, %s, 'sap', 1, 'statistics', 'text', FALSE, 'Some SAP text.', NULL, 'k1')",
            (f"{_HAS_SAP_TEXT}:sap:0", _HAS_SAP_TEXT),
        )
    connection.commit()
    yield connection
    connection.rollback()
    with connection.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE nct_id = ANY(%s)", (list(nct_ids),))
        cur.execute("DELETE FROM trials WHERE nct_id = ANY(%s)", (list(nct_ids),))
    connection.commit()
    connection.close()


@pytest.mark.db
def test_unposted_document_uses_real_chunk_presence_not_has_sap_flag(
    conn: psycopg.Connection,
) -> None:
    nct_ids = [_HAS_SAP_TEXT, _NO_SAP_TEXT_BUT_FLAGGED_TRUE, _HAS_SAP_FALSE]

    questions = generate_adversarial_questions(conn, nct_ids, target_count=4)

    unposted_nct_ids = {q.nct_id for q in questions if q.category == "unposted_document"}
    # _HAS_SAP_TEXT actually has retrievable SAP text -- must never be
    # selected for an "unposted document" question despite being a valid
    # candidate trial otherwise.
    assert _HAS_SAP_TEXT not in unposted_nct_ids
    # Both trials with zero real SAP chunks are eligible, regardless of
    # what their has_sap flag says.
    assert _NO_SAP_TEXT_BUT_FLAGGED_TRUE in unposted_nct_ids
    assert _HAS_SAP_FALSE in unposted_nct_ids


@pytest.mark.db
def test_fact_not_in_corpus_questions_reference_the_trial(conn: psycopg.Connection) -> None:
    nct_ids = [_HAS_SAP_TEXT]
    questions = generate_adversarial_questions(conn, nct_ids, target_count=2)

    facts = [q for q in questions if q.category == "fact_not_in_corpus"]
    assert facts
    for q in facts:
        assert q.nct_id in q.question_text
        assert q.expected_behavior == "refuse"


# --- refusal_metrics: pure, no DB -------------------------------------

_ADVERSARIAL = [
    AdversarialQuestion(
        question_id="a1",
        nct_id="NCT01",
        question_text="What is the manufacturing lot number for NCT01?",
        category="fact_not_in_corpus",
    ),
    AdversarialQuestion(
        question_id="a2",
        nct_id="NCT02",
        question_text="What does the SAP say about missing data for NCT02?",
        category="unposted_document",
    ),
]

_ANSWERABLE = [
    EvalQuestion(
        question_id="t1",
        nct_id="NCT03",
        question_text="What phase is NCT03?",
        gold_answer="Phase 2",
        gold_chunk_ids=["NCT03:protocol:0"],
    ),
    EvalQuestion(
        question_id="t2",
        nct_id="NCT04",
        question_text="Who is the sponsor of NCT04?",
        gold_answer="Acme Inc",
        gold_chunk_ids=["NCT04:protocol:0"],
    ),
]


def test_refusal_metrics_hedge_is_not_counted_as_a_clean_refusal() -> None:
    responses = {
        "What is the manufacturing lot number for NCT01?": "NOT_ANSWERABLE",
        "What does the SAP say about missing data for NCT02?": (
            "I'm not sure, but possibly it uses multiple imputation."
        ),
        "What phase is NCT03?": "Phase 2",
        "Who is the sponsor of NCT04?": "NOT_ANSWERABLE",
    }

    scores = refusal_metrics(_ADVERSARIAL, _ANSWERABLE, generate_fn=responses.__getitem__)

    # Only the exact NOT_ANSWERABLE (a1) counts as a correct refusal; the
    # hedge on a2 does not, so refusal_accuracy is 1/2, not 2/2.
    assert scores.n_adversarial == 2
    assert scores.refusal_accuracy == pytest.approx(0.5)
    # Only NCT04's exact NOT_ANSWERABLE counts as an over-refusal.
    assert scores.n_answerable_sample == 2
    assert scores.over_refusal_rate == pytest.approx(0.5)


def test_refusal_metrics_perfect_behavior() -> None:
    responses = {
        "What is the manufacturing lot number for NCT01?": "NOT_ANSWERABLE",
        "What does the SAP say about missing data for NCT02?": "NOT_ANSWERABLE",
        "What phase is NCT03?": "Phase 2",
        "Who is the sponsor of NCT04?": "Acme Inc",
    }

    scores = refusal_metrics(_ADVERSARIAL, _ANSWERABLE, generate_fn=responses.__getitem__)

    assert scores.refusal_accuracy == pytest.approx(1.0)
    assert scores.over_refusal_rate == pytest.approx(0.0)


def test_refusal_metrics_empty_sets_are_zero_not_error() -> None:
    scores = refusal_metrics([], [], generate_fn=lambda _: "NOT_ANSWERABLE")
    assert scores.refusal_accuracy == 0.0
    assert scores.over_refusal_rate == 0.0
