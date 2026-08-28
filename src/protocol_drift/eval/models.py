"""Shared eval-question schema -- S3-04.

T1 (auto-generated, S3-03) and T2 (hand-written, S3-04) share the same core
shape (question_id, nct_id, question_text, gold_answer, gold_chunk_ids) but
diverge on one field each: T1 carries `template_id` (which template
produced it), T2 carries `gold_answer_notes` (the human labeler's
supporting quote/context, used by S3-08's judge calibration, not by
exact-match scoring). `EvalQuestion` makes both optional so either file's
rows validate against one model -- `extra="forbid"` still catches a typoed
field name in a hand-entered T2 row at load time instead of surfacing as a
mysterious scorer bug later in S3-07/S3-08.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EvalQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    nct_id: str
    question_text: str
    gold_answer: str
    gold_chunk_ids: list[str]
    template_id: str | None = None
    gold_answer_notes: str | None = None
