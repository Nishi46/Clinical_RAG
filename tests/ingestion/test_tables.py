import json
from pathlib import Path

import fitz
import pytest

from protocol_drift.ingestion.tables import (
    _overlapping_section_label,
    _parse_header,
    _span_lookup,
    extract_document_tables,
    extract_page_tables,
    propagate_headers,
    tables_corpus,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdfs"
ASSESSMENT_SCHEDULE_PDF = FIXTURES / "NCT02872116_protocol.pdf"

# corpus_assessment.md Sec.6: Table 5.1-2, confirmed spanning 0-indexed
# pages 84-85 (caption + header repeated on both pages), Table 5.1-3
# starting page 86. Page 61 has a real table with no embedded caption --
# used to test the preceding-page-text caption fallback.
TABLE_5_1_2_PAGE_1 = 84
TABLE_5_1_2_PAGE_2 = 85
NO_CAPTION_TABLE_PAGE = 61


# --- _span_lookup -- pure geometry -----------------------------------------


def test_span_lookup_detects_colspan_and_rowspan() -> None:
    # a 2x2 grid where the top row is one merged cell (colspan=2) and the
    # bottom-left cell spans down into a notional third row (rowspan=2)
    cells = [
        (0.0, 0.0, 20.0, 10.0),  # row0: one cell spanning both columns
        (0.0, 10.0, 10.0, 30.0),  # row1-2, col0: spans two row-bands
        (10.0, 10.0, 20.0, 20.0),  # row1, col1
        (10.0, 20.0, 20.0, 30.0),  # row2, col1
    ]

    lookup = _span_lookup(cells)

    assert lookup[(0, 0)] == (1, 2)  # colspan=2
    assert lookup[(1, 0)] == (2, 1)  # rowspan=2
    assert lookup[(1, 1)] == (1, 1)
    assert lookup[(2, 1)] == (1, 1)


# --- _parse_header -- unit carrying -----------------------------------------


def test_parse_header_extracts_trailing_unit() -> None:
    assert _parse_header("Weight (kg)") == {"text": "Weight (kg)", "unit": "kg"}
    assert _parse_header("BMI (kg/m2)") == {"text": "BMI (kg/m2)", "unit": "kg/m2"}


def test_parse_header_no_unit_when_no_trailing_parenthetical() -> None:
    assert _parse_header("Procedure") == {"text": "Procedure", "unit": None}


def test_parse_header_does_not_treat_cross_reference_as_unit() -> None:
    # a multi-word parenthetical (spaces) is not a unit -- avoids
    # mistaking "(see Section 4.5.1.6)"-style cross-references for units
    result = _parse_header("Note (see Section 4.5.1.6)")
    assert result["unit"] is None


# --- _overlapping_section_label ---------------------------------------------


def test_overlapping_section_label_finds_containing_section() -> None:
    sections = [
        {"label": "study_design", "page_range": [0, 10]},
        {"label": "assessment_schedule", "page_range": [80, 90]},
    ]
    assert _overlapping_section_label([84, 84], sections) == "assessment_schedule"


def test_overlapping_section_label_defaults_unclassified_when_no_match() -> None:
    sections = [{"label": "study_design", "page_range": [0, 10]}]
    assert _overlapping_section_label([84, 84], sections) == "unclassified"


def test_overlapping_section_label_defaults_unclassified_when_no_sections() -> None:
    assert _overlapping_section_label([84, 84], None) == "unclassified"


# --- extract_page_tables: real fixture, embedded caption --------------------


def test_extract_page_tables_known_assessment_schedule_table() -> None:
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        tables = extract_page_tables(doc[TABLE_5_1_2_PAGE_1])
    finally:
        doc.close()

    assert len(tables) == 1
    table = tables[0]
    expected_caption = (
        "Table 5.1-2: On-Treatment Assessments - "
        "Subjects in Nivolumab-plus-Ipilimumab Arm (CA209649)"
    )
    assert table["caption"] == expected_caption
    assert table["headers"][0]["text"] == "Procedure"
    assert len(table["headers"]) == 5


def test_extract_page_tables_redacted_row_merges_without_duplicating() -> None:
    # corpus_assessment.md Sec.6/Sec.8: the confirmed real redaction --
    # "Collection of biomarker sampling" row's schedule cells are blacked
    # out. Geometrically this is one colspan=4 merged cell with empty text,
    # not 4 separate empty cells and not duplicated content.
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        table = extract_page_tables(doc[TABLE_5_1_2_PAGE_1])[0]
    finally:
        doc.close()

    redacted_cells = [
        cell for row in table["rows"] for cell in row if cell["colspan"] == 4 and cell["text"] == ""
    ]
    assert len(redacted_cells) == 1
    assert redacted_cells[0]["rowspan"] == 1


def test_extract_page_tables_see_note_rowspan_not_duplicated() -> None:
    # "See Note" appears twice in this table for two different reasons --
    # the Tumor Imaging Assessment row (row 6) has two genuinely separate,
    # unmerged "See Note" cells (rowspan=1 each; they just happen to share
    # identical text), while the FACT-Ga/EQ-5D-3L cells (row 9) are a real
    # rowspan=2 merge. Confirmed via cell geometry, not assumed from text.
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        table = extract_page_tables(doc[TABLE_5_1_2_PAGE_1])[0]
    finally:
        doc.close()

    see_note_cells = [cell for row in table["rows"] for cell in row if cell["text"] == "See Note"]
    assert len(see_note_cells) == 4
    merged = [c for c in see_note_cells if c["rowspan"] == 2]
    unmerged = [c for c in see_note_cells if c["rowspan"] == 1]
    assert len(merged) == 2  # row 9: FACT-Ga / EQ-5D-3L, genuinely spanned
    assert len(unmerged) == 2  # row 6: Tumor Imaging Assessment, not spanned
    assert {c["col"] for c in merged} == {2, 3}
    assert {c["col"] for c in unmerged} == {2, 3}


def test_extract_page_tables_cross_reference_text_preserved_as_plain_text() -> None:
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        table = extract_page_tables(doc[TABLE_5_1_2_PAGE_1])[0]
    finally:
        doc.close()

    all_text = " ".join(cell["text"] for row in table["rows"] for cell in row)
    assert "See Table 5.5-1" in all_text


def test_extract_page_tables_second_page_repeats_header() -> None:
    # Confirmed real: unlike a table that omits its header on a
    # continuation page, this document's authors repeated both the caption
    # and header row verbatim on page 85 too.
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        page1_table = extract_page_tables(doc[TABLE_5_1_2_PAGE_1])[0]
        page2_table = extract_page_tables(doc[TABLE_5_1_2_PAGE_2])[0]
    finally:
        doc.close()

    assert page1_table["caption"] == page2_table["caption"]
    assert [h["text"] for h in page1_table["headers"]] == [
        h["text"] for h in page2_table["headers"]
    ]


# --- extract_page_tables: caption fallback via preceding page text --------


def test_extract_page_tables_no_caption_without_page_blocks() -> None:
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        tables = extract_page_tables(doc[NO_CAPTION_TABLE_PAGE])
    finally:
        doc.close()

    assert len(tables) == 1
    assert tables[0]["caption"] is None
    assert tables[0]["headers"][0]["text"].startswith("Product Description")


def test_extract_page_tables_caption_fallback_from_preceding_block() -> None:
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        table_bbox = doc[NO_CAPTION_TABLE_PAGE].find_tables().tables[0].bbox
        table_top = table_bbox[1]
        page_blocks = [
            {
                "bbox": [89.0, table_top - 20, 400.0, table_top - 4],
                "text": "Table 4.2-1: Investigational Product Description",
            }
        ]
        tables = extract_page_tables(doc[NO_CAPTION_TABLE_PAGE], page_blocks)
    finally:
        doc.close()

    assert tables[0]["caption"] == "Table 4.2-1: Investigational Product Description"


def test_extract_page_tables_caption_fallback_ignores_blocks_below_table() -> None:
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        table_bbox = doc[NO_CAPTION_TABLE_PAGE].find_tables().tables[0].bbox
        table_bottom = table_bbox[3]
        page_blocks = [
            {
                "bbox": [89.0, table_bottom + 4, 400.0, table_bottom + 20],
                "text": "Table 99-1: Not actually above the table",
            }
        ]
        tables = extract_page_tables(doc[NO_CAPTION_TABLE_PAGE], page_blocks)
    finally:
        doc.close()

    assert tables[0]["caption"] is None


# --- propagate_headers -------------------------------------------------------


def test_propagate_headers_fills_in_missing_headers_for_same_caption() -> None:
    headered = {"caption": "Table 1", "headers": [{"text": "A", "unit": None}], "rows": []}
    headerless = {"caption": "Table 1", "headers": [None], "rows": []}

    result = propagate_headers([headered, headerless])

    assert result[0] == headered  # untouched
    assert result[1]["headers"] == headered["headers"]
    assert result[1]["headers_propagated"] is True


def test_propagate_headers_leaves_different_caption_untouched() -> None:
    headered = {"caption": "Table 1", "headers": [{"text": "A", "unit": None}], "rows": []}
    other = {"caption": "Table 2", "headers": [], "rows": []}

    result = propagate_headers([headered, other])

    assert result[1]["headers"] == []
    assert "headers_propagated" not in result[1]


def test_propagate_headers_no_caption_never_propagated() -> None:
    headered = {"caption": "Table 1", "headers": [{"text": "A", "unit": None}], "rows": []}
    no_caption = {"caption": None, "headers": [], "rows": []}

    result = propagate_headers([headered, no_caption])

    assert result[1]["headers"] == []
    assert "headers_propagated" not in result[1]


# --- extract_document_tables: real fixture, source_section join -----------


def test_extract_document_tables_joins_source_section() -> None:
    from protocol_drift.ingestion.extract import extract_document

    content = extract_document(ASSESSMENT_SCHEDULE_PDF, ["born_digital"] * 171)
    content["nct_id"] = "NCT02872116"
    content["doc_type"] = "protocol"
    content["source_path"] = str(ASSESSMENT_SCHEDULE_PDF)
    sections = [
        {"label": "study_design", "page_range": [0, 50]},
        {"label": "assessment_schedule", "page_range": [80, 106]},
    ]

    tables, errors = extract_document_tables(ASSESSMENT_SCHEDULE_PDF, content, sections=sections)

    assert errors == []
    page_84_table = next(t for t in tables if t["page_range"] == [84, 84])
    assert page_84_table["source_section"] == "assessment_schedule"


def test_extract_document_tables_defaults_unclassified_without_sections() -> None:
    from protocol_drift.ingestion.extract import extract_document

    content = extract_document(ASSESSMENT_SCHEDULE_PDF, ["born_digital"] * 171)
    content["nct_id"] = "NCT02872116"
    content["doc_type"] = "protocol"
    content["source_path"] = str(ASSESSMENT_SCHEDULE_PDF)

    tables, errors = extract_document_tables(ASSESSMENT_SCHEDULE_PDF, content, sections=None)

    page_84_table = next(t for t in tables if t["page_range"] == [84, 84])
    assert page_84_table["source_section"] == "unclassified"


def test_extract_document_tables_logs_page_errors_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import protocol_drift.ingestion.tables as tables_module
    from protocol_drift.ingestion.extract import extract_document

    real_extract_page_tables = tables_module.extract_page_tables

    def fake_extract_page_tables(page, page_blocks=None):
        if page.number == TABLE_5_1_2_PAGE_1:
            raise fitz.mupdf.FzErrorFormat("simulated bad table grid")
        return real_extract_page_tables(page, page_blocks)

    monkeypatch.setattr(tables_module, "extract_page_tables", fake_extract_page_tables)

    content = extract_document(ASSESSMENT_SCHEDULE_PDF, ["born_digital"] * 171)
    content["nct_id"] = "NCT02872116"
    content["doc_type"] = "protocol"
    content["source_path"] = str(ASSESSMENT_SCHEDULE_PDF)

    tables, errors = extract_document_tables(ASSESSMENT_SCHEDULE_PDF, content, sections=None)

    assert len(errors) == 1
    assert errors[0][0] == TABLE_5_1_2_PAGE_1
    assert "simulated bad table grid" in errors[0][1]
    # the other known table (page 85) still extracted despite page 84 failing
    assert any(t["page_range"] == [85, 85] for t in tables)
    assert not any(t["page_range"] == [84, 84] for t in tables)


# --- tables_corpus orchestration --------------------------------------------


def test_tables_corpus_writes_output_and_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import protocol_drift.ingestion.tables as tables_module

    def fake_extract_document_tables(pdf_path, document_content, sections=None):
        return [
            {
                "page_range": [0, 0],
                "caption": None,
                "headers": [],
                "rows": [],
                "source_section": "x",
            }
        ], []

    monkeypatch.setattr(tables_module, "extract_document_tables", fake_extract_document_tables)

    extracted_dir = tmp_path / "extracted"
    (extracted_dir / "NCT00000001").mkdir(parents=True)
    (extracted_dir / "NCT00000001" / "protocol.json").write_text(
        json.dumps(
            {
                "nct_id": "NCT00000001",
                "doc_type": "protocol",
                "source_path": "irrelevant.pdf",
                "total_pages": 1,
                "pages": [{"page_number": 0, "blocks": []}],
            }
        )
    )
    dest_dir = tmp_path / "tables"

    summary = tables_corpus(
        extracted_dir=extracted_dir, sections_dir=tmp_path / "sections", dest_dir=dest_dir
    )

    assert summary == {"documents": 1, "tables": 1, "failed_pages": 0}
    payload = json.loads((dest_dir / "NCT00000001" / "protocol.json").read_text())
    assert payload["nct_id"] == "NCT00000001"
    assert len(payload["tables"]) == 1


def test_tables_corpus_logs_page_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import protocol_drift.ingestion.tables as tables_module

    def fake_extract_document_tables(pdf_path, document_content, sections=None):
        return [], [(3, "simulated failure")]

    monkeypatch.setattr(tables_module, "extract_document_tables", fake_extract_document_tables)

    extracted_dir = tmp_path / "extracted"
    (extracted_dir / "NCT00000001").mkdir(parents=True)
    (extracted_dir / "NCT00000001" / "protocol.json").write_text(
        json.dumps(
            {
                "nct_id": "NCT00000001",
                "doc_type": "protocol",
                "source_path": "irrelevant.pdf",
                "total_pages": 1,
                "pages": [{"page_number": 0, "blocks": []}],
            }
        )
    )
    dest_dir = tmp_path / "tables"

    summary = tables_corpus(
        extracted_dir=extracted_dir, sections_dir=tmp_path / "sections", dest_dir=dest_dir
    )

    assert summary["failed_pages"] == 1
    error_log = (dest_dir / "extraction_failures.log").read_text()
    assert "NCT00000001\tprotocol\t3\tsimulated failure" in error_log
