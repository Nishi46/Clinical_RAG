import json
from pathlib import Path

import pytest

from protocol_drift.db.extract import extract_amendments
from protocol_drift.ingestion.extract import extract_document
from protocol_drift.ingestion.versioning import (
    _header_footer_text,
    document_version_timeline,
    extract_page_version_marker,
    lookup_registry_amendments,
    mark_superseded,
    reconcile_with_registry,
    version_corpus,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
PDF_FIXTURES = FIXTURES / "pdfs"
BMS_PDF = PDF_FIXTURES / "NCT02872116_protocol.pdf"  # corpus_assessment.md Sec.6
BMS_HISTORY = FIXTURES / "NCT02872116_history.json"


# --- extract_page_version_marker --------------------------------------------


def test_extract_page_version_marker_revised_protocol_no() -> None:
    marker = extract_page_version_marker("Revised Protocol No.: 09 Date: 16-Sep-2019 85")
    assert marker == {
        "version": 9,
        "raw_version": "09",
        "date": "16-Sep-2019",
        "pattern": "revised_protocol",
    }


def test_extract_page_version_marker_revised_protocol_number() -> None:
    marker = extract_page_version_marker("Revised Protocol Number: 3")
    assert marker is not None
    assert marker["version"] == 3
    assert marker["pattern"] == "revised_protocol"


def test_extract_page_version_marker_amendment() -> None:
    marker = extract_page_version_marker("Protocol Amendment 12")
    assert marker is not None
    assert marker["version"] == 12
    assert marker["pattern"] == "amendment"


def test_extract_page_version_marker_dotted_version() -> None:
    marker = extract_page_version_marker("Version 2.1")
    assert marker is not None
    assert marker["version"] == 2.1
    assert marker["pattern"] == "version"


def test_extract_page_version_marker_no_match_returns_none() -> None:
    assert extract_page_version_marker("Confidential -- do not distribute") is None


def test_extract_page_version_marker_amendment_with_hash() -> None:
    # Confirmed real footer convention (NCT03007979): "Amendment #5", not
    # "Amendment 5".
    marker = extract_page_version_marker("Protocol Version: 04/12/2019 Amendment #5 Page 6 of 68")
    assert marker is not None
    assert marker["version"] == 5
    assert marker["pattern"] == "amendment"


def test_extract_page_version_marker_amendment_fused_to_unspaced_date_is_none() -> None:
    # Confirmed real (NCT03056755's SAP): "SAP Amendment 3" and "24-Mar-2022"
    # render with zero space between them ("...Amendment 324-Mar-2022"),
    # since S2-01 joins block spans without inserting a separator. A bare
    # \d+ would misread this as amendment 324; the correct behavior is no
    # marker at all, not a differently-wrong shorter guess like 32.
    text = "NovartisConfidentialPage 2SAP Amendment 324-Mar-2022 (3:54)"
    assert extract_page_version_marker(text) is None


def test_extract_page_version_marker_sas_version_is_not_a_document_version() -> None:
    # Confirmed real (NCT03040115/NCT03085238's SAPs): "...using SAS
    # version 9.4" is the analysis software's version, not the document's
    # own -- must not be mistaken for one just because it matches "version
    # N.N" in the footer/header band.
    text = "STATISTICAL SOFTWARE outputs will be generated using SAS version 9.4."
    assert extract_page_version_marker(text) is None


def test_extract_page_version_marker_version_does_not_match_trailing_date() -> None:
    # Confirmed real false positive this guards against: without the
    # "Amendment #N" that normally follows, "Version: 04/12/2019" alone
    # must not mis-extract "04" (the date's day component) as version 4.
    assert extract_page_version_marker("Protocol Version: 04/12/2019") is None


def test_extract_page_version_marker_version_does_not_match_dotted_date() -> None:
    # Confirmed real (NCT03069313): "Version: 02.19.16" is a dot-separated
    # MM.DD.YY date (Feb 19 2016), not a "2.19" document version.
    assert extract_page_version_marker("Version: 02.19.16") is None


def test_extract_page_version_marker_no_date_leaves_date_none() -> None:
    marker = extract_page_version_marker("Amendment 4")
    assert marker is not None
    assert marker["date"] is None


# --- document_version_timeline: real fixture, known footer -----------------


def test_document_version_timeline_bms_known_footer() -> None:
    doc = extract_document(BMS_PDF, ["born_digital"] * 171)
    doc["source_path"] = str(BMS_PDF)

    timeline = document_version_timeline(doc, pdf_path=BMS_PDF)

    # Title/signature pages (0-1) carry no footer marker; every content page
    # from 2-170 repeats "Revised Protocol No.: 09" -- confirmed by directly
    # scanning the fixture's page text, not assumed from prose.
    assert timeline[0] == ([0, 1], None)
    assert timeline[1][0] == [2, 170]
    assert timeline[1][1] is not None
    assert timeline[1][1]["version"] == 9
    assert len(timeline) == 2


def test_header_footer_text_excludes_long_body_paragraph_in_band() -> None:
    # Confirmed real false positive: a body paragraph ending "...SAS for
    # Windows, version 9.4, Cary, NC." (NCT03040115's SAP) can itself start
    # low enough on a lightly-filled page to land inside the bottom margin
    # band by y0 alone -- length, not just position, must gate inclusion.
    long_paragraph = "All analyses will be done by using SAS for Windows, " * 5 + "version 9.4."
    page_record = {
        "blocks": [
            {"bbox": [0, 700, 500, 780], "text": long_paragraph},
            {"bbox": [0, 730, 500, 750], "text": "Protocol Version: 3 Page 9 of 68"},
        ]
    }

    text = _header_footer_text(page_record, page_height=792.0)

    assert "9.4" not in text
    assert "Protocol Version: 3" in text


def test_document_version_timeline_no_marker_document_is_all_none() -> None:
    content = {
        "nct_id": "NCT99999999",
        "doc_type": "protocol",
        "total_pages": 2,
        "source_path": str(BMS_PDF),
        "pages": [
            {"page_number": 0, "blocks": [{"bbox": [0, 300, 100, 320], "text": "body text"}]},
            {"page_number": 1, "blocks": [{"bbox": [0, 300, 100, 320], "text": "more body text"}]},
        ],
    }

    timeline = document_version_timeline(content, pdf_path=BMS_PDF)

    assert timeline == [([0, 1], None)]


# --- mark_superseded ----------------------------------------------------------


def test_mark_superseded_flags_only_strictly_older_ranges() -> None:
    v8 = {"version": 8, "raw_version": "08", "date": None, "pattern": "amendment"}
    v9 = {"version": 9, "raw_version": "09", "date": None, "pattern": "amendment"}
    timeline = [([0, 10], v8), ([11, 20], None), ([21, 30], v9)]

    records = mark_superseded(timeline)

    assert records[0] == {"page_range": [0, 10], "version_marker": v8, "superseded": True}
    assert records[1] == {"page_range": [11, 20], "version_marker": None, "superseded": False}
    assert records[2] == {"page_range": [21, 30], "version_marker": v9, "superseded": False}


def test_mark_superseded_no_markers_at_all_none_superseded() -> None:
    timeline = [([0, 5], None)]
    assert mark_superseded(timeline) == [
        {"page_range": [0, 5], "version_marker": None, "superseded": False}
    ]


# --- reconcile_with_registry ---------------------------------------------------


def _marker(version: int) -> dict:
    return {"version": version, "raw_version": str(version), "date": None, "pattern": "amendment"}


def test_reconcile_with_registry_no_doc_version_is_unresolvable() -> None:
    registry = [{"version": 0, "date": None, "modules_changed": []}]
    result = reconcile_with_registry([([0, 5], None)], registry)
    assert result["status"] == "unresolvable"
    assert result["doc_version"] is None


def test_reconcile_with_registry_no_registry_rows_is_unresolvable() -> None:
    result = reconcile_with_registry([([0, 5], _marker(3))], [])
    assert result["status"] == "unresolvable"
    assert result["doc_version"] == 3
    assert result["registry_version_range"] is None


def test_reconcile_with_registry_within_range_is_agreement() -> None:
    registry = [{"version": v, "date": None, "modules_changed": []} for v in range(0, 20)]
    result = reconcile_with_registry([([0, 5], _marker(9))], registry)
    assert result["status"] == "agreement"
    assert result["doc_version"] == 9
    assert result["registry_version_range"] == [0, 19]


def test_reconcile_with_registry_above_max_is_mismatch() -> None:
    registry = [{"version": v, "date": None, "modules_changed": []} for v in range(0, 3)]
    result = reconcile_with_registry([([0, 5], _marker(9))], registry)
    assert result["status"] == "mismatch"
    assert result["registry_version_range"] == [0, 2]


def test_reconcile_with_registry_bms_spot_check_against_real_history_fixture() -> None:
    # S2-07's hand spot-check, automated: NCT02872116's footer tags version
    # 9, and its real registry history (92 revisions, 0-91) makes that
    # plausible -- corpus_assessment.md Sec.6 / field_paths.md.
    doc = extract_document(BMS_PDF, ["born_digital"] * 171)
    doc["source_path"] = str(BMS_PDF)
    timeline = document_version_timeline(doc, pdf_path=BMS_PDF)

    history = json.loads(BMS_HISTORY.read_text())
    registry_amendments = extract_amendments("NCT02872116", history)
    assert len(registry_amendments) == 92

    result = reconcile_with_registry(timeline, registry_amendments)

    assert result["doc_version"] == 9
    assert result["registry_version_range"] == [0, 91]
    assert result["status"] == "agreement"


# --- lookup_registry_amendments: real DB -----------------------------------


@pytest.mark.db
def test_lookup_registry_amendments_against_real_amendments_table() -> None:
    # Seeded by S1-05's full-cohort load; NCT03007407 has 10 known real rows.
    result = lookup_registry_amendments(["NCT03007407"])

    rows = result["NCT03007407"]
    assert len(rows) == 10
    assert sorted(r["version"] for r in rows) == list(range(10))


@pytest.mark.db
def test_lookup_registry_amendments_empty_input_returns_empty_without_querying() -> None:
    assert lookup_registry_amendments([]) == {}


# --- version_corpus orchestration --------------------------------------------


def _write_extracted(dest_dir: Path, nct_id: str, doc_type: str, total_pages: int = 2) -> None:
    (dest_dir / nct_id).mkdir(parents=True, exist_ok=True)
    (dest_dir / nct_id / f"{doc_type}.json").write_text(
        json.dumps(
            {
                "nct_id": nct_id,
                "doc_type": doc_type,
                "source_path": "irrelevant.pdf",
                "total_pages": total_pages,
                "pages": [{"page_number": i, "blocks": []} for i in range(total_pages)],
            }
        )
    )


def test_version_corpus_writes_records_and_flags_low_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import protocol_drift.ingestion.versioning as versioning_module

    def fake_timeline(content: dict, pdf_path: Path | None = None) -> list:
        if content["nct_id"] == "NCT00000001":
            return [([0, 1], _marker(3))]  # full coverage
        return [([0, 1], None)]  # no marker at all -- low coverage

    monkeypatch.setattr(versioning_module, "document_version_timeline", fake_timeline)

    extracted_dir = tmp_path / "extracted"
    _write_extracted(extracted_dir, "NCT00000001", "protocol")
    _write_extracted(extracted_dir, "NCT00000002", "sap")
    dest_dir = tmp_path / "versions"

    registry_rows = [{"version": v, "date": None, "modules_changed": []} for v in range(5)]
    registry = {"NCT00000001": registry_rows}
    summary = version_corpus(
        extracted_dir=extracted_dir, dest_dir=dest_dir, registry_lookup=lambda ids: registry
    )

    assert summary == {"documents": 2, "low_coverage": 1, "failed": 0}

    payload_1 = json.loads((dest_dir / "NCT00000001" / "protocol.json").read_text())
    assert payload_1["versions"] == [
        {"page_range": [0, 1], "version_marker": _marker(3), "superseded": False}
    ]
    assert payload_1["reconciliation"]["status"] == "agreement"

    warnings_log = (dest_dir / "version_warnings.log").read_text().splitlines()
    assert warnings_log == ["NCT00000002\tsap"]


def test_version_corpus_logs_errors_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fitz

    import protocol_drift.ingestion.versioning as versioning_module

    def fake_timeline(content: dict, pdf_path: Path | None = None) -> list:
        if content["nct_id"] == "NCT00000001":
            raise fitz.mupdf.FzErrorFormat("cannot find object in xref")
        return [([0, 1], _marker(1))]

    monkeypatch.setattr(versioning_module, "document_version_timeline", fake_timeline)

    extracted_dir = tmp_path / "extracted"
    _write_extracted(extracted_dir, "NCT00000001", "protocol")
    _write_extracted(extracted_dir, "NCT00000002", "sap")
    dest_dir = tmp_path / "versions"

    summary = version_corpus(
        extracted_dir=extracted_dir, dest_dir=dest_dir, registry_lookup=lambda ids: {}
    )

    assert summary["documents"] == 1
    assert summary["failed"] == 1
    error_log = (dest_dir / "versioning_errors.log").read_text()
    assert "NCT00000001" in error_log
