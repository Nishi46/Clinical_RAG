import json
from pathlib import Path

import pytest

from protocol_drift.reports.corpus_report import (
    doc_type_breakdown,
    gate_bracket,
    generate_corpus_report,
    page_count_distribution,
    page_count_histogram,
)


@pytest.mark.parametrize(
    ("pct", "expected_bracket"),
    [
        (0.0, "< 15%"),
        (14.9, "< 15%"),
        (15.0, "15-40%"),
        (40.0, "15-40%"),
        (40.1, "> 40%"),
        (90.0, "> 40%"),
    ],
)
def test_gate_bracket_boundaries(pct: float, expected_bracket: str) -> None:
    bracket, _action = gate_bracket(pct)
    assert bracket == expected_bracket


def test_gate_bracket_action_text_matches_sprint_plan() -> None:
    _, action = gate_bracket(5.0)
    assert "footnote" in action
    _, action = gate_bracket(25.0)
    assert "S2-03" in action
    _, action = gate_bracket(50.0)
    assert "Re-select" in action


def test_page_count_distribution() -> None:
    docs = [{"total_pages": p} for p in [10, 20, 30, 40, 50]]
    dist = page_count_distribution(docs)
    assert dist == {"min": 10, "median": 30, "mean": 30.0, "max": 50}


def test_page_count_distribution_empty() -> None:
    assert page_count_distribution([]) == {"min": 0, "median": 0, "mean": 0, "max": 0}


def test_page_count_histogram_buckets() -> None:
    docs = [{"total_pages": p} for p in [5, 45, 50, 99, 100]]
    hist = page_count_histogram(docs, bucket_size=50)
    assert hist == [("0-49", 2), ("50-99", 2), ("100-149", 1)]


def test_doc_type_breakdown() -> None:
    entries = [{"doc_type": "protocol"}, {"doc_type": "sap"}, {"doc_type": "protocol"}]
    assert doc_type_breakdown(entries) == {"protocol": 2, "sap": 1}


def test_md_table_escapes_pipe_in_cell_content() -> None:
    # stratum keys like "INDUSTRY|PHASE1|PHASE2" contain literal "|" -- an
    # unescaped one would be parsed as an extra column separator by any
    # Markdown renderer and break the table.
    from protocol_drift.reports.corpus_report import _md_table

    table = _md_table(["Stratum", "Trials"], [["INDUSTRY|PHASE1|PHASE2", 9]])
    data_line = table.splitlines()[2]

    assert data_line == "| INDUSTRY\\|PHASE1\\|PHASE2 | 9 |"


def _write_fixtures(tmp_path: Path) -> tuple[Path, Path, Path]:
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(
        json.dumps(
            {
                "count": 2,
                "stratification_summary": {"INDUSTRY|PHASE3": 1, "OTHER|NA": 1},
                "trials": [{"nct_id": "NCT00000001"}, {"nct_id": "NCT00000002"}],
            }
        )
    )

    pdf_manifest_path = tmp_path / "pdf_manifest.json"
    pdf_manifest_path.write_text(
        json.dumps(
            {
                "entries": [
                    {"nct_id": "NCT00000001", "doc_type": "protocol"},
                    {"nct_id": "NCT00000001", "doc_type": "sap"},
                    {"nct_id": "NCT00000002", "doc_type": "protocol"},
                ]
            }
        )
    )

    classification_path = tmp_path / "classification.json"
    classification_path.write_text(
        json.dumps(
            {
                "summary": {
                    "documents": 3,
                    "failed_documents": 0,
                    "total_pages": 300,
                    "total_scanned_pages": 6,
                    "scanned_page_pct_page_level": 2.0,
                    "born_digital_doc_pct": 66.7,
                    "document_level_counts": {"born_digital": 2, "mixed": 1},
                    "s0_03_comparison": {
                        "sample_page_level_scanned_pct": 1.1,
                        "sample_born_digital_doc_pct": 88.0,
                        "page_level_flag": None,
                        "born_digital_doc_flag": None,
                    },
                },
                "documents": [
                    {"nct_id": "NCT00000001", "doc_type": "protocol", "total_pages": 100},
                    {"nct_id": "NCT00000001", "doc_type": "sap", "total_pages": 50},
                    {"nct_id": "NCT00000002", "doc_type": "protocol", "total_pages": 150},
                ],
            }
        )
    )

    return cohort_path, pdf_manifest_path, classification_path


def test_generate_corpus_report_writes_markdown_with_gate_verdict(tmp_path: Path) -> None:
    cohort_path, pdf_manifest_path, classification_path = _write_fixtures(tmp_path)
    out_path = tmp_path / "corpus.md"

    report = generate_corpus_report(
        cohort_path=cohort_path,
        pdf_manifest_path=pdf_manifest_path,
        classification_path=classification_path,
        out_path=out_path,
    )

    assert out_path.read_text() == report
    assert "GATE S1-G1" in report
    assert "< 15%" in report
    assert "2.0%" in report  # the page-level scanned rate, not hand-typed elsewhere
    assert "protocol" in report and "sap" in report
    assert "INDUSTRY\\|PHASE3" in report  # escaped so the "|" doesn't break the table


def test_generate_corpus_report_surfaces_divergence_note(tmp_path: Path) -> None:
    cohort_path, pdf_manifest_path, classification_path = _write_fixtures(tmp_path)
    classification = json.loads(classification_path.read_text())
    classification["summary"]["s0_03_comparison"]["page_level_flag"] = (
        "page-level scanned rate: full-cohort 2.69% diverges >2x from S0-03 sample 1.1%"
    )
    classification_path.write_text(json.dumps(classification))
    out_path = tmp_path / "corpus.md"

    report = generate_corpus_report(
        cohort_path=cohort_path,
        pdf_manifest_path=pdf_manifest_path,
        classification_path=classification_path,
        out_path=out_path,
    )

    assert "**Note:**" in report
    assert "diverges >2x" in report
