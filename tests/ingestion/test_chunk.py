import json
from pathlib import Path

import fitz

from protocol_drift.ingestion.assessment_schedule import reassemble_document_tables
from protocol_drift.ingestion.chunk import (
    _chunk_blocks,
    _render_table_text,
    _split_table_rows,
    _token_count,
    chunk_corpus,
    chunk_document,
    write_chunks,
)
from protocol_drift.ingestion.extract import extract_document
from protocol_drift.ingestion.tables import extract_page_tables, propagate_headers

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdfs"
ASSESSMENT_SCHEDULE_PDF = FIXTURES / "NCT02872116_protocol.pdf"


def _section(label: str, page_range: list[int]) -> dict:
    return {
        "label": label,
        "raw_heading_text": label,
        "page_range": page_range,
        "detection_method": "bookmark",
    }


def _header(text: str) -> dict:
    return {"text": text, "unit": None}


def _table(
    page_range: list[int], caption: str, headers: list[dict], rows: list, source_section: str
) -> dict:
    return {
        "page_range": page_range,
        "bbox": None,
        "caption": caption,
        "headers": headers,
        "rows": rows,
        "source_section": source_section,
    }


def _page(page_number: int, texts: list[str]) -> dict:
    return {
        "page_number": page_number,
        "page_class": "born_digital",
        "needs_ocr": False,
        "has_redaction": False,
        "blocks": [{"bbox": [0, 0, 1, 1], "text": t} for t in texts],
    }


def _row(text: str, col: int = 0) -> list[dict]:
    return [{"col": col, "text": text, "rowspan": 1, "colspan": 1}]


# --- _chunk_blocks: block-boundary packing -----------------------------------


def test_chunk_blocks_never_splits_a_block() -> None:
    blocks = [
        (0, {"text": " ".join(f"w{i}" for i in range(300))}),
        (0, {"text": " ".join(f"x{i}" for i in range(300))}),
    ]

    chunks = _chunk_blocks(blocks, chunk_tokens=400)

    assert len(chunks) == 2
    assert "w299" in chunks[0]["text"] and "x0" not in chunks[0]["text"]
    assert "x299" in chunks[1]["text"] and "w0" not in chunks[1]["text"]


def test_chunk_blocks_oversized_single_block_stays_whole() -> None:
    huge = " ".join(f"w{i}" for i in range(1000))
    blocks = [(0, {"text": huge})]

    chunks = _chunk_blocks(blocks, chunk_tokens=100)

    assert len(chunks) == 1
    assert _token_count(chunks[0]["text"]) == 1000


def test_chunk_blocks_page_range_spans_contributing_pages() -> None:
    blocks = [(2, {"text": "a b c"}), (3, {"text": "d e f"})]

    chunks = _chunk_blocks(blocks, chunk_tokens=100)

    assert chunks[0]["page_range"] == [2, 3]


def test_chunk_blocks_empty_input_produces_no_chunks() -> None:
    assert _chunk_blocks([], chunk_tokens=100) == []


# --- table rendering + row-splitting -----------------------------------------


def test_render_table_text_includes_caption_header_and_units() -> None:
    age_cell = {"col": 0, "text": "34", "rowspan": 1, "colspan": 1}
    weight_cell = {"col": 1, "text": "70", "rowspan": 1, "colspan": 1}
    table = _table(
        [0, 0],
        "Table 1: Demographics",
        [_header("Age"), _header("Weight (kg)")],
        [[age_cell, weight_cell]],
        "unclassified",
    )

    text = _render_table_text(table)

    assert "Table 1: Demographics" in text
    assert "Weight (kg)" in text
    assert "34 | 70" in text


def test_split_table_rows_never_splits_a_row_and_repeats_header_budget() -> None:
    headers = [_header("Procedure")]
    rows = [_row(f"row {i}") for i in range(10)]
    table = _table([0, 0], "Table 9", headers, rows, "assessment_schedule")

    groups = _split_table_rows(table, table_chunk_tokens=10)

    assert len(groups) > 1
    # every row appears exactly once, across all groups, in order
    flattened = [r for group in groups for r in group]
    assert flattened == rows
    # no group is empty (would render as a headerless/rowless chunk)
    assert all(group for group in groups)


# --- chunk_document: section boundaries are hard breaks ----------------------


def test_chunk_document_never_mixes_two_sections_in_one_chunk() -> None:
    document_content = {
        "nct_id": "NCT00000001",
        "doc_type": "protocol",
        "total_pages": 2,
        "pages": [
            _page(0, [" ".join(f"alpha{i}" for i in range(50))]),
            _page(1, [" ".join(f"beta{i}" for i in range(50))]),
        ],
    }
    sections = [_section("synopsis", [0, 0]), _section("background", [1, 1])]

    chunks = chunk_document(document_content, sections, tables=[], versions=[], chunk_tokens=512)

    assert len(chunks) == 2
    assert chunks[0]["section"] == "synopsis"
    assert "alpha0" in chunks[0]["text"] and "beta0" not in chunks[0]["text"]
    assert chunks[1]["section"] == "background"
    assert "beta0" in chunks[1]["text"] and "alpha0" not in chunks[1]["text"]


def test_chunk_document_large_section_still_respects_token_budget() -> None:
    document_content = {
        "nct_id": "NCT00000001",
        "doc_type": "protocol",
        "total_pages": 1,
        "pages": [_page(0, [" ".join(f"w{i}" for i in range(50))] * 3)],
    }
    sections = [_section("background", [0, 0])]

    chunks = chunk_document(document_content, sections, tables=[], versions=[], chunk_tokens=80)

    # 3 blocks of 50 tokens, budget 80: each next block would push the
    # running total past 80, so every block starts its own chunk -> 3.
    assert len(chunks) == 3
    assert all(c["section"] == "background" for c in chunks)


# --- chunk_document: contextual header prefix + version lookup --------------


def test_chunk_document_text_carries_contextual_header_with_version() -> None:
    document_content = {
        "nct_id": "NCT00000001",
        "doc_type": "protocol",
        "total_pages": 1,
        "pages": [_page(0, ["some body text"])],
    }
    sections = [_section("synopsis", [0, 0])]
    versions = [{"page_range": [0, 0], "version_marker": {"version": 9}, "superseded": False}]

    chunks = chunk_document(document_content, sections, tables=[], versions=versions)

    assert chunks[0]["text"].startswith("[NCT00000001 | protocol v9 | synopsis]\n")
    assert chunks[0]["doc_version"] == 9


def test_chunk_document_unknown_version_renders_placeholder() -> None:
    document_content = {
        "nct_id": "NCT00000001",
        "doc_type": "protocol",
        "total_pages": 1,
        "pages": [_page(0, ["some body text"])],
    }
    sections = [_section("synopsis", [0, 0])]

    chunks = chunk_document(document_content, sections, tables=[], versions=[])

    assert chunks[0]["text"].startswith("[NCT00000001 | protocol v? | synopsis]\n")
    assert chunks[0]["doc_version"] is None


# --- chunk_document: table handling ------------------------------------------


def test_chunk_document_small_table_becomes_one_table_chunk() -> None:
    document_content = {
        "nct_id": "NCT00000001",
        "doc_type": "protocol",
        "total_pages": 1,
        "pages": [_page(0, [])],
    }
    sections = [_section("eligibility", [0, 0])]
    table = _table([0, 0], "Table 1", [_header("Criterion")], [_row("Age >= 18")], "eligibility")

    chunks = chunk_document(document_content, sections, tables=[table], versions=[])

    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "table"
    assert chunks[0]["section"] == "eligibility"
    assert "Age >= 18" in chunks[0]["text"]


def test_chunk_document_assessment_schedule_table_gets_its_own_chunk_type() -> None:
    document_content = {
        "nct_id": "NCT00000001",
        "doc_type": "protocol",
        "total_pages": 1,
        "pages": [_page(0, [])],
    }
    sections = [_section("assessment_schedule", [0, 0])]
    table = _table(
        [0, 0], "Table 5", [_header("Visit")], [_row("Screening")], "assessment_schedule"
    )

    chunks = chunk_document(document_content, sections, tables=[table], versions=[])

    assert chunks[0]["chunk_type"] == "assessment_schedule"


def test_chunk_document_oversized_table_splits_by_row_never_headerless() -> None:
    document_content = {
        "nct_id": "NCT00000001",
        "doc_type": "protocol",
        "total_pages": 1,
        "pages": [_page(0, [])],
    }
    sections = [_section("assessment_schedule", [0, 0])]
    headers = [_header("Procedure")]
    rows = [_row(f"procedure number {i} with extra padding words here") for i in range(20)]
    table = _table([0, 0], "Table 5.1-2", headers, rows, "assessment_schedule")

    chunks = chunk_document(
        document_content, sections, tables=[table], versions=[], table_chunk_tokens=30
    )

    assert len(chunks) > 1
    assert all(c["chunk_type"] == "assessment_schedule" for c in chunks)
    # every chunk carries the header line -- never a headerless row
    assert all("Procedure" in c["text"] for c in chunks)
    # no row lost or duplicated across the split
    total_procedure_lines = sum(c["text"].count("procedure number") for c in chunks)
    assert total_procedure_lines == 20
    # every chunk still stays within the section that contains the table
    assert all(c["section"] == "assessment_schedule" for c in chunks)


def test_chunk_document_table_excludes_its_pages_from_surrounding_text() -> None:
    document_content = {
        "nct_id": "NCT00000001",
        "doc_type": "protocol",
        "total_pages": 3,
        "pages": [
            _page(0, ["intro text"]),
            _page(1, ["duplicated table cell text that should not leak into a text chunk"]),
            _page(2, ["closing text"]),
        ],
    }
    sections = [_section("assessment_schedule", [0, 2])]
    table = _table([1, 1], "Table 1", [_header("Col")], [_row("cell")], "assessment_schedule")

    chunks = chunk_document(document_content, sections, tables=[table], versions=[])

    text_chunks = [c for c in chunks if c["chunk_type"] != "assessment_schedule"]
    assert all("duplicated table cell text" not in c["text"] for c in text_chunks)


# --- chunk_document: the required enforcement test, real fixture ------------


def _real_bms_tables_5_1_series() -> tuple[dict, list[dict]]:
    """The confirmed real NCT02872116 assessment-schedule table run
    (corpus_assessment.md Sec.6): pages 82-94 (0-indexed), five logical
    tables after S2-06 reassembly, Table 5.1-3 alone rendering to ~800
    whitespace tokens -- already past the 512 plain-text budget."""
    content = extract_document(ASSESSMENT_SCHEDULE_PDF, ["born_digital"] * 171)
    sliced = {
        "nct_id": "NCT02872116",
        "doc_type": "protocol",
        "total_pages": 13,
        "pages": content["pages"][82:95],
    }
    doc = fitz.open(ASSESSMENT_SCHEDULE_PDF)
    try:
        raw_tables = propagate_headers(
            [t for i in range(82, 95) for t in extract_page_tables(doc[i])]
        )
    finally:
        doc.close()
    for t in raw_tables:
        t["source_section"] = "assessment_schedule"
    merged = reassemble_document_tables(raw_tables)
    return sliced, merged


def test_chunk_document_does_not_split_known_table_past_512_tokens() -> None:
    sliced, merged = _real_bms_tables_5_1_series()
    table_5_1_3 = next(t for t in merged if (t["caption"] or "").startswith("Table 5.1-3"))
    assert table_5_1_3["page_range"] == [86, 90]
    assert len(table_5_1_3["rows"]) == 23
    # sanity: this table really would overflow the plain 512-token budget,
    # which is exactly why S2-08 needs a raised, separate table ceiling.
    assert _token_count(_render_table_text(table_5_1_3)) > 512

    page_numbers = [p["page_number"] for p in sliced["pages"]]
    sections = [_section("assessment_schedule", [min(page_numbers), max(page_numbers)])]

    chunks = chunk_document(sliced, sections, tables=merged, versions=[])

    matching = [c for c in chunks if c["page_range"] == [86, 90]]
    assert len(matching) == 1, "Table 5.1-3 must land in exactly one chunk, not be split"
    assert matching[0]["chunk_type"] == "assessment_schedule"
    assert matching[0]["text"].count("On-Treatment Assessments") >= 1


# --- write_chunks -------------------------------------------------------------


def test_write_chunks_one_json_object_per_line(tmp_path: Path) -> None:
    chunks = [
        {"nct_id": "NCT00000001", "doc_type": "protocol", "chunk_index": 0, "text": "a"},
        {"nct_id": "NCT00000001", "doc_type": "protocol", "chunk_index": 1, "text": "b"},
    ]
    dest_path = tmp_path / "NCT00000001" / "protocol.jsonl"

    write_chunks(chunks, dest_path)

    lines = dest_path.read_text().splitlines()
    assert [json.loads(line)["chunk_index"] for line in lines] == [0, 1]


# --- chunk_corpus orchestration -----------------------------------------------


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_chunk_corpus_writes_chunks_for_every_extracted_document(tmp_path: Path) -> None:
    extracted_dir = tmp_path / "extracted"
    sections_dir = tmp_path / "sections"
    tables_dir = tmp_path / "tables"
    versions_dir = tmp_path / "versions"
    dest_dir = tmp_path / "chunks"

    _write_json(
        extracted_dir / "NCT00000001" / "protocol.json",
        {
            "nct_id": "NCT00000001",
            "doc_type": "protocol",
            "total_pages": 1,
            "pages": [_page(0, ["hello world"])],
        },
    )
    _write_json(
        sections_dir / "NCT00000001" / "protocol.json",
        {
            "nct_id": "NCT00000001",
            "doc_type": "protocol",
            "sections": [_section("synopsis", [0, 0])],
        },
    )

    summary = chunk_corpus(
        extracted_dir=extracted_dir,
        sections_dir=sections_dir,
        tables_dir=tables_dir,
        versions_dir=versions_dir,
        dest_dir=dest_dir,
    )

    assert summary == {"documents": 1, "chunks": 1}
    dest_path = dest_dir / "NCT00000001" / "protocol.jsonl"
    assert dest_path.exists()
    chunk = json.loads(dest_path.read_text().splitlines()[0])
    assert chunk["section"] == "synopsis"


def test_chunk_corpus_missing_sections_degrades_to_whole_document(tmp_path: Path) -> None:
    extracted_dir = tmp_path / "extracted"
    dest_dir = tmp_path / "chunks"
    _write_json(
        extracted_dir / "NCT00000002" / "sap.json",
        {
            "nct_id": "NCT00000002",
            "doc_type": "sap",
            "total_pages": 1,
            "pages": [_page(0, ["hello world"])],
        },
    )

    chunk_corpus(
        extracted_dir=extracted_dir,
        sections_dir=tmp_path / "sections",
        tables_dir=tmp_path / "tables",
        versions_dir=tmp_path / "versions",
        dest_dir=dest_dir,
    )

    dest_path = dest_dir / "NCT00000002" / "sap.jsonl"
    chunk = json.loads(dest_path.read_text().splitlines()[0])
    assert chunk["section"] == "unclassified"
