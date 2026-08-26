import json
from pathlib import Path

import fitz

from protocol_drift.ingestion.assessment_schedule import (
    is_continuation_table,
    merge_tables,
    reassemble_corpus,
    reassemble_document_tables,
)
from protocol_drift.ingestion.tables import extract_page_tables

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdfs"
ASSESSMENT_SCHEDULE_PDF = FIXTURES / "NCT02872116_protocol.pdf"

# corpus_assessment.md Sec.6, confirmed by directly counting rows per page
# via S2-05's own extractor (not assumed from prose): Table 5.1-1 is a
# single page (82); Table 5.1-2 spans 0-indexed pages 83-85 (9+10+3=22 data
# rows); Table 5.1-3 spans 86-90 (8+4+7+3+1=23 data rows); Table 5.1-4
# spans 91-93 (9+7+3=19 data rows); Table 5.1-5 is a single page (94).
TABLE_RUN_PAGE_START = 82
TABLE_RUN_PAGE_END = 95  # exclusive


def _header(text: str) -> dict:
    return {"text": text, "unit": None}


# --- is_continuation_table ---------------------------------------------------


def test_is_continuation_table_matching_headers_adjacent_pages() -> None:
    table_a = {"page_range": [83, 83], "headers": [_header("Procedure"), _header("Visit 1")]}
    table_b = {
        "page_range": [84, 84],
        "headers": [_header("Procedure"), _header("Visit 1")],
        "caption": "Table 5.1-2: On-Treatment Assessments",
    }
    assert is_continuation_table(table_a, table_b) is True


def test_is_continuation_table_non_adjacent_no_marker_is_false() -> None:
    table_a = {"page_range": [83, 83], "headers": [_header("Procedure")]}
    table_b = {"page_range": [90, 90], "headers": [_header("Procedure")], "caption": "Table X"}
    assert is_continuation_table(table_a, table_b) is False


def test_is_continuation_table_non_adjacent_with_continued_marker_is_true() -> None:
    table_a = {"page_range": [83, 83], "headers": [_header("Procedure")]}
    table_b = {
        "page_range": [90, 90],
        "headers": [_header("Procedure")],
        "caption": "Table 5.1-2 (continued)",
    }
    assert is_continuation_table(table_a, table_b) is True


def test_is_continuation_table_different_headers_adjacent_is_false() -> None:
    # confirmed real: Table 5.1-2's last page (85) is immediately followed
    # by Table 5.1-3's first page (86), but their headers differ -- header
    # equality, not adjacency alone, is what correctly separates them.
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        table_5_1_2_last_page = extract_page_tables(doc[85])[0]
        table_5_1_3_first_page = extract_page_tables(doc[86])[0]
    finally:
        doc.close()

    assert table_5_1_2_last_page["page_range"][1] + 1 == table_5_1_3_first_page["page_range"][0]
    assert is_continuation_table(table_5_1_2_last_page, table_5_1_3_first_page) is False


def test_is_continuation_table_both_headerless_is_false() -> None:
    table_a = {"page_range": [1, 1], "headers": []}
    table_b = {"page_range": [2, 2], "headers": [], "caption": None}
    assert is_continuation_table(table_a, table_b) is False


# --- merge_tables -------------------------------------------------------------


def test_merge_tables_concatenates_rows_in_page_order() -> None:
    headers = [_header("Procedure"), _header("Visit 1")]
    table_a = {
        "page_range": [10, 10],
        "bbox": [0, 0, 10, 10],
        "caption": "Table 9: Schedule",
        "headers": headers,
        "rows": [[{"col": 0, "text": "Row A1", "rowspan": 1, "colspan": 1}]],
        "source_section": "assessment_schedule",
    }
    table_b = {
        "page_range": [11, 11],
        "bbox": [0, 0, 10, 10],
        "caption": "Table 9: Schedule",
        "headers": headers,
        "rows": [[{"col": 0, "text": "Row B1", "rowspan": 1, "colspan": 1}]],
        "source_section": "assessment_schedule",
    }

    merged = merge_tables([table_a, table_b])

    assert merged["page_range"] == [10, 11]
    assert merged["bbox"] is None
    assert merged["caption"] == "Table 9: Schedule"
    assert merged["headers"] == headers
    assert merged["rows"] == [table_a["rows"][0], table_b["rows"][0]]
    assert merged["source_section"] == "assessment_schedule"
    assert merged["merged_from_pages"] == [10, 11]


def test_merge_tables_preserves_headers_propagated_flag() -> None:
    headers = [_header("Procedure")]
    table_a = {
        "page_range": [1, 1],
        "caption": "Table 1",
        "headers": headers,
        "rows": [],
        "headers_propagated": True,
    }
    table_b = {"page_range": [2, 2], "caption": "Table 1", "headers": headers, "rows": []}

    merged = merge_tables([table_a, table_b])

    assert merged["headers_propagated"] is True


# --- reassemble_document_tables: real fixture, full known table run -------


def test_reassemble_document_tables_known_run() -> None:
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        raw_tables = [
            t
            for i in range(TABLE_RUN_PAGE_START, TABLE_RUN_PAGE_END)
            for t in extract_page_tables(doc[i])
        ]
    finally:
        doc.close()

    merged = reassemble_document_tables(raw_tables)

    assert len(merged) == 5
    by_caption = {(t["caption"] or "")[:13]: t for t in merged}

    table_5_1_1 = by_caption["Table 5.1-1: "]
    assert table_5_1_1["page_range"] == [82, 82]
    assert len(table_5_1_1["rows"]) == 11
    assert "merged_from_pages" not in table_5_1_1  # untouched, not merge_tables' output

    table_5_1_2 = by_caption["Table 5.1-2: "]
    assert table_5_1_2["page_range"] == [83, 85]
    assert len(table_5_1_2["rows"]) == 22
    assert table_5_1_2["merged_from_pages"] == [83, 84, 85]

    table_5_1_3 = by_caption["Table 5.1-3: "]
    assert table_5_1_3["page_range"] == [86, 90]
    assert len(table_5_1_3["rows"]) == 23
    assert table_5_1_3["merged_from_pages"] == [86, 87, 88, 89, 90]

    table_5_1_4 = by_caption["Table 5.1-4: "]
    assert table_5_1_4["page_range"] == [91, 93]
    assert len(table_5_1_4["rows"]) == 19

    table_5_1_5 = by_caption["Table 5.1-5: "]
    assert table_5_1_5["page_range"] == [94, 94]
    assert len(table_5_1_5["rows"]) == 9
    assert "merged_from_pages" not in table_5_1_5


def test_reassemble_document_tables_empty_input() -> None:
    assert reassemble_document_tables([]) == []


def test_reassemble_document_tables_single_table_untouched() -> None:
    table = {"page_range": [0, 0], "caption": None, "headers": [], "rows": []}
    assert reassemble_document_tables([table]) == [table]


# --- reassemble_corpus orchestration ----------------------------------------


def _raw_table(page: int, caption: str, header_text: str = "Procedure") -> dict:
    return {
        "page_range": [page, page],
        "bbox": [0, 0, 1, 1],
        "caption": caption,
        "headers": [_header(header_text)],
        "rows": [[{"col": 0, "text": f"row on page {page}", "rowspan": 1, "colspan": 1}]],
        "source_section": "assessment_schedule",
    }


def test_reassemble_corpus_merges_and_writes_raw_pages(tmp_path: Path) -> None:
    tables_dir = tmp_path / "tables"
    (tables_dir / "NCT00000001").mkdir(parents=True)
    dest_path = tables_dir / "NCT00000001" / "protocol.json"
    dest_path.write_text(
        json.dumps(
            {
                "nct_id": "NCT00000001",
                "doc_type": "protocol",
                "tables": [
                    _raw_table(0, "Table 1: Schedule"),
                    _raw_table(1, "Table 1: Schedule"),
                    _raw_table(2, "Table 2: Unrelated", header_text="Outcome"),
                ],
            }
        )
    )

    summary = reassemble_corpus(tables_dir=tables_dir)

    assert summary == {"documents": 1, "raw_tables": 3, "merged_tables": 2}
    payload = json.loads(dest_path.read_text())
    assert len(payload["_raw_pages"]) == 3
    assert len(payload["tables"]) == 2
    assert payload["tables"][0]["page_range"] == [0, 1]
    assert payload["tables"][0]["merged_from_pages"] == [0, 1]
    assert payload["tables"][1]["page_range"] == [2, 2]


def test_reassemble_corpus_is_idempotent_on_rerun(tmp_path: Path) -> None:
    tables_dir = tmp_path / "tables"
    (tables_dir / "NCT00000001").mkdir(parents=True)
    dest_path = tables_dir / "NCT00000001" / "protocol.json"
    dest_path.write_text(
        json.dumps(
            {
                "nct_id": "NCT00000001",
                "doc_type": "protocol",
                "tables": [
                    _raw_table(0, "Table 1: Schedule"),
                    _raw_table(1, "Table 1: Schedule"),
                ],
            }
        )
    )

    first = reassemble_corpus(tables_dir=tables_dir)
    second = reassemble_corpus(tables_dir=tables_dir)

    assert first == second == {"documents": 1, "raw_tables": 2, "merged_tables": 1}
    payload = json.loads(dest_path.read_text())
    assert len(payload["tables"]) == 1
    assert payload["tables"][0]["rows"] == [
        _raw_table(0, "x")["rows"][0],
        _raw_table(1, "x")["rows"][0],
    ]
