import json
from pathlib import Path

from protocol_drift.reports.ingestion_report import (
    _md_table,
    chunk_stats,
    count_detection_failures,
    document_depth_split,
    generate_ingestion_report,
    naive_vs_aware_pair,
    ocr_backlog_stats,
    section_detection_rate,
    section_detection_rate_by_sponsor_class,
    table_reassembly_stats,
)

UNCLASSIFIED = "unclassified"


def _section_doc(nct_id: str, labels: list[str]) -> dict:
    return {
        "nct_id": nct_id,
        "doc_type": "protocol",
        "sections": [
            {
                "label": label,
                "raw_heading_text": label,
                "page_range": [i, i],
                "detection_method": "bookmark",
            }
            for i, label in enumerate(labels)
        ],
    }


# --- section_detection_rate ---------------------------------------------------


def test_section_detection_rate_counts_documents_with_any_named_section() -> None:
    docs = [
        _section_doc("NCT1", ["synopsis"]),
        _section_doc("NCT2", [UNCLASSIFIED]),
        _section_doc("NCT3", [UNCLASSIFIED, "ethics"]),
    ]
    result = section_detection_rate(docs)
    assert result == {"documents": 3, "detected": 2, "rate": 66.7}


def test_section_detection_rate_empty_is_zero_not_a_division_error() -> None:
    assert section_detection_rate([]) == {"documents": 0, "detected": 0, "rate": 0.0}


# --- section_detection_rate_by_sponsor_class ----------------------------------


def test_section_detection_rate_by_sponsor_class_groups_correctly() -> None:
    docs = [
        _section_doc("NCT1", ["synopsis"]),
        _section_doc("NCT2", [UNCLASSIFIED]),
        _section_doc("NCT3", ["ethics"]),
    ]
    sponsor_lookup = {
        "NCT1": {"sponsor_class": "INDUSTRY"},
        "NCT2": {"sponsor_class": "INDUSTRY"},
        "NCT3": {"sponsor_class": "NIH"},
    }

    result = section_detection_rate_by_sponsor_class(docs, sponsor_lookup)

    assert result == {
        "INDUSTRY": {"documents": 2, "detected": 1, "rate": 50.0},
        "NIH": {"documents": 1, "detected": 1, "rate": 100.0},
    }


def test_section_detection_rate_by_sponsor_class_missing_lookup_is_unknown() -> None:
    docs = [_section_doc("NCT1", ["synopsis"])]

    result = section_detection_rate_by_sponsor_class(docs, sponsor_lookup={})

    assert result == {"UNKNOWN": {"documents": 1, "detected": 1, "rate": 100.0}}


# --- document_depth_split ------------------------------------------------------


def test_document_depth_split_buckets_zero_full_and_partial() -> None:
    docs = [
        _section_doc("NCT1", [UNCLASSIFIED]),  # zero
        _section_doc("NCT2", [UNCLASSIFIED, "synopsis"]),  # partial (has a gap)
        _section_doc("NCT3", ["synopsis", "ethics"]),  # full coverage, no gap
    ]

    result = document_depth_split(docs)

    assert result["zero_sections"] == 1
    assert result["partial"] == 1
    assert result["full_coverage"] == 1
    assert result["documents"] == 3


def test_document_depth_split_empty_produces_zero_percentages() -> None:
    result = document_depth_split([])
    assert result["zero_sections_pct"] == 0.0
    assert result["full_coverage_pct"] == 0.0
    assert result["partial_pct"] == 0.0


# --- count_detection_failures --------------------------------------------------


def test_count_detection_failures_counts_nonblank_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "detection_failures.log"
    log_path.write_text("NCT1\tprotocol\tAcme\tINDUSTRY\nNCT2\tsap\tUNKNOWN\tUNKNOWN\n")

    assert count_detection_failures(log_path) == 2


def test_count_detection_failures_missing_file_is_zero(tmp_path: Path) -> None:
    assert count_detection_failures(tmp_path / "missing.log") == 0


# --- table_reassembly_stats -----------------------------------------------------


def test_table_reassembly_stats_sums_raw_vs_logical() -> None:
    docs = [
        {"nct_id": "NCT1", "tables": [{"page_range": [0, 2]}], "_raw_pages": [{}, {}, {}]},
        {"nct_id": "NCT2", "tables": [], "_raw_pages": []},
    ]

    result = table_reassembly_stats(docs)

    assert result == {
        "documents": 2,
        "documents_with_tables": 1,
        "raw_tables": 3,
        "logical_tables": 1,
        "collapsed_by_reassembly": 2,
    }


def test_table_reassembly_stats_falls_back_to_tables_when_no_raw_pages_key() -> None:
    docs = [{"nct_id": "NCT1", "tables": [{"page_range": [0, 0]}]}]

    result = table_reassembly_stats(docs)

    assert result["raw_tables"] == 1
    assert result["collapsed_by_reassembly"] == 0


# --- chunk_stats -----------------------------------------------------------------


def _write_chunks(path: Path, chunks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(c) for c in chunks) + ("\n" if chunks else ""))


def test_chunk_stats_computes_mean_median_types_and_is_ocr(tmp_path: Path) -> None:
    doc_a = tmp_path / "NCT1" / "protocol.jsonl"
    doc_b = tmp_path / "NCT2" / "sap.jsonl"
    _write_chunks(
        doc_a,
        [
            {"chunk_type": "text", "is_ocr": False},
            {"chunk_type": "table", "is_ocr": True},
        ],
    )
    _write_chunks(doc_b, [{"chunk_type": "text", "is_ocr": False}])

    result = chunk_stats([doc_a, doc_b])

    assert result["documents"] == 2
    assert result["total_chunks"] == 3
    assert result["mean_per_doc"] == 1.5
    assert result["median_per_doc"] == 1.5
    assert result["type_counts"] == {"table": 1, "text": 2}
    assert result["is_ocr_chunks"] == 1


def test_chunk_stats_empty_file_counts_as_zero_chunks(tmp_path: Path) -> None:
    doc_a = tmp_path / "NCT1" / "protocol.jsonl"
    _write_chunks(doc_a, [])

    result = chunk_stats([doc_a])

    assert result["documents"] == 1
    assert result["total_chunks"] == 0
    assert result["mean_per_doc"] == 0.0


def test_chunk_stats_no_files_produces_zeros_not_a_statistics_error() -> None:
    assert chunk_stats([]) == {
        "documents": 0,
        "total_chunks": 0,
        "mean_per_doc": 0.0,
        "median_per_doc": 0,
        "type_counts": {},
        "is_ocr_chunks": 0,
    }


# --- ocr_backlog_stats -----------------------------------------------------------


def test_ocr_backlog_stats_counts_pages_and_distinct_documents() -> None:
    backlog = {
        "pages": [
            {"nct_id": "NCT1", "doc_type": "protocol", "page_number": 3},
            {"nct_id": "NCT1", "doc_type": "protocol", "page_number": 4},
            {"nct_id": "NCT2", "doc_type": "sap", "page_number": 0},
        ]
    }

    assert ocr_backlog_stats(backlog) == {"pages": 3, "documents": 2}


def test_ocr_backlog_stats_missing_pages_key_is_empty() -> None:
    assert ocr_backlog_stats({}) == {"pages": 0, "documents": 0}


# --- naive_vs_aware_pair -------------------------------------------------------


def test_naive_vs_aware_pair_finds_overlapping_naive_and_matching_aware_chunk(
    tmp_path: Path,
) -> None:
    chunks_naive_dir = tmp_path / "chunks_naive"
    chunks_dir = tmp_path / "chunks"
    _write_chunks(
        chunks_naive_dir / "NCT02872116" / "protocol.jsonl",
        [
            {"chunk_index": 0, "page_range": [80, 85], "text": "before"},
            {"chunk_index": 1, "page_range": [85, 88], "text": "mid-table cut part one"},
            {"chunk_index": 2, "page_range": [88, 90], "text": "mid-table cut part two"},
            {"chunk_index": 3, "page_range": [91, 95], "text": "after"},
        ],
    )
    aware_chunk = {
        "chunk_index": 10,
        "page_range": [86, 90],
        "chunk_type": "assessment_schedule",
        "text": "clean",
    }
    _write_chunks(chunks_dir / "NCT02872116" / "protocol.jsonl", [aware_chunk])

    pair = naive_vs_aware_pair(chunks_naive_dir, chunks_dir, page_range=(86, 90))

    assert [c["chunk_index"] for c in pair["naive_chunks"]] == [1, 2]
    assert len(pair["aware_chunks"]) == 1
    assert pair["aware_chunks"][0]["chunk_index"] == 10


def test_naive_vs_aware_pair_missing_files_returns_empty_lists(tmp_path: Path) -> None:
    pair = naive_vs_aware_pair(tmp_path / "missing_naive", tmp_path / "missing_aware")
    assert pair["naive_chunks"] == []
    assert pair["aware_chunks"] == []


# --- _md_table -------------------------------------------------------------------


def test_md_table_escapes_pipe_in_cell_content() -> None:
    table = _md_table(["A", "B"], [["x|y", 1]])
    assert table.splitlines()[2] == "| x\\|y | 1 |"


# --- generate_ingestion_report: end-to-end with fixtures -----------------------


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_generate_ingestion_report_end_to_end(tmp_path: Path) -> None:
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(
        json.dumps({"trials": [{"nct_id": "NCT00000001"}, {"nct_id": "NCT00000002"}]})
    )

    sections_dir = tmp_path / "sections"
    _write_json(
        sections_dir / "NCT00000001" / "protocol.json",
        _section_doc("NCT00000001", ["synopsis", "ethics"]),
    )
    _write_json(
        sections_dir / "NCT00000002" / "sap.json", _section_doc("NCT00000002", [UNCLASSIFIED])
    )
    (sections_dir / "detection_failures.log").write_text(
        "NCT00000002\tsap\tAcme Pharma\tINDUSTRY\n"
    )

    tables_dir = tmp_path / "tables"
    _write_json(
        tables_dir / "NCT00000001" / "protocol.json",
        {"nct_id": "NCT00000001", "tables": [{"page_range": [0, 1]}], "_raw_pages": [{}, {}]},
    )

    chunks_dir = tmp_path / "chunks"
    _write_chunks(
        chunks_dir / "NCT00000001" / "protocol.jsonl",
        [{"chunk_index": 0, "page_range": [0, 0], "chunk_type": "text", "is_ocr": False}],
    )

    chunks_naive_dir = tmp_path / "chunks_naive"

    ocr_backlog_path = tmp_path / "ocr_backlog.json"
    ocr_backlog_path.write_text(
        json.dumps({"pages": [{"nct_id": "NCT00000002", "doc_type": "sap", "page_number": 5}]})
    )

    out_path = tmp_path / "ingestion.md"

    def fake_sponsor_lookup(nct_ids: list[str]) -> dict:
        return {"NCT00000001": {"sponsor_class": "INDUSTRY"}}

    report = generate_ingestion_report(
        cohort_path=cohort_path,
        sections_dir=sections_dir,
        tables_dir=tables_dir,
        chunks_dir=chunks_dir,
        chunks_naive_dir=chunks_naive_dir,
        ocr_backlog_path=ocr_backlog_path,
        out_path=out_path,
        sponsor_lookup_fn=fake_sponsor_lookup,
    )

    assert out_path.read_text() == report
    assert "Ingestion quality report" in report
    assert "50.0%" in report  # 1 of 2 documents detected
    assert "INDUSTRY" in report
    assert "collapsed_by_reassembly" not in report  # internal key name, not report prose
    assert "No naive chunks found" in report  # comparison doc absent from these fixtures


def test_generate_ingestion_report_missing_ocr_backlog_defaults_to_empty(tmp_path: Path) -> None:
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(json.dumps({"trials": [{"nct_id": "NCT00000001"}]}))
    sections_dir = tmp_path / "sections"
    _write_json(
        sections_dir / "NCT00000001" / "protocol.json",
        _section_doc("NCT00000001", ["synopsis"]),
    )

    report = generate_ingestion_report(
        cohort_path=cohort_path,
        sections_dir=sections_dir,
        tables_dir=tmp_path / "tables",
        chunks_dir=tmp_path / "chunks",
        chunks_naive_dir=tmp_path / "chunks_naive",
        ocr_backlog_path=tmp_path / "missing_backlog.json",
        out_path=tmp_path / "ingestion.md",
        sponsor_lookup_fn=lambda ids: {},
    )

    assert "**0** page(s)" in report
