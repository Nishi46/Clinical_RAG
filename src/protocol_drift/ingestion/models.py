"""Formal Chunk schema -- S2-09.

Every prior ingestion module (S2-01 through S2-08) deliberately passes
around plain dicts, matching this codebase's convention elsewhere. This is
the one place that convention is intentionally broken: `data/chunks/` is
the pipeline's terminal artifact -- what retrieval and discrepancy
detection actually consume in later sprints -- so it's worth a real,
enforced schema rather than another ad-hoc dict shape. chunk_type and
doc_type are both closed sets by construction elsewhere in the pipeline
(S2-08 only ever emits "text"/"table"/"assessment_schedule"; S2-01 only
ever ingests "protocol"/"sap", ICF excluded per scratch/pdf_notes.md) --
`Literal` enforces that invariant here instead of leaving it as an
unchecked convention that a future change could silently violate.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Chunk(BaseModel):
    """sprint_plan.md's field set (nct_id, doc_type, doc_version, section,
    subsection, page_range, chunk_type, is_ocr), plus the two fields S2-08's
    chunker already needs to produce something embeddable/orderable
    (chunk_index, text). `extra="forbid"` so a stray or renamed field in a
    future chunker change fails loudly here rather than silently riding
    along in data/chunks/."""

    model_config = ConfigDict(extra="forbid")

    nct_id: str
    doc_type: Literal["protocol", "sap"]
    doc_version: int | float | None
    section: str
    subsection: str | None
    page_range: tuple[int, int]
    chunk_type: Literal["text", "table", "assessment_schedule"]
    is_ocr: bool
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _page_range_is_ordered(self) -> Chunk:
        start, end = self.page_range
        if start > end:
            raise ValueError(f"page_range start {start} > end {end}")
        return self
