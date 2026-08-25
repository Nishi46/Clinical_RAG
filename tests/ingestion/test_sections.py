import json
from pathlib import Path

import pytest

from protocol_drift.ingestion.extract import extract_document
from protocol_drift.ingestion.sections import (
    UNCLASSIFIED,
    _body_font_size,
    _is_heading_candidate,
    _sections_from_markers,
    lookup_sponsors,
    match_canonical_label,
    segment_corpus,
    segment_document,
    write_detection_failures_log,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdfs"
NOVARTIS_PDF = FIXTURES / "NCT02798211_protocol.pdf"  # corpus_assessment.md Sec.3
BMS_PDF = FIXTURES / "NCT02872116_protocol.pdf"
CAPRICOR_PDF = FIXTURES / "NCT02485938_protocol.pdf"
NO_BOOKMARKS_PDF = FIXTURES / "NCT04311632_sap.pdf"  # confirmed empty get_toc()


# --- match_canonical_label -- confirmed real level-1 bookmark titles -----


def test_match_canonical_label_ethics_novartis_and_bms() -> None:
    # corpus_assessment.md Sec.3: same concept, different literal heading
    # and different position, across sponsors.
    assert match_canonical_label("10 Ethical considerations") == "ethics"
    assert match_canonical_label("2 ETHICAL CONSIDERATIONS") == "ethics"


def test_match_canonical_label_capricor_has_no_distinct_ethics_heading() -> None:
    # corpus_assessment.md Sec.3: Capricor's TOC folds ethics into
    # "Administrative Considerations" rather than a distinct top-level
    # entry -- confirmed against the real fixture's TOC, not assumed.
    assert match_canonical_label("11 Administrative Considerations") == "administrative"
    assert match_canonical_label("10 Independent Oversight Committees") is None


def test_match_canonical_label_assessment_schedule_all_three_sponsors() -> None:
    assert match_canonical_label("6 Visit schedule and assessments") == "assessment_schedule"
    assert match_canonical_label("5 STUDY ASSESSMENTS AND PROCEDURES") == "assessment_schedule"
    assert match_canonical_label("6 Study Procedures") == "assessment_schedule"
    assert match_canonical_label("7 Study Activities") == "assessment_schedule"


def test_match_canonical_label_statistics_all_three_sponsors() -> None:
    assert match_canonical_label("9 Data analysis") == "statistics"
    assert match_canonical_label("8 STATISTICAL CONSIDERATIONS") == "statistics"
    assert match_canonical_label("9 Planned Statistical Methods") == "statistics"


def test_match_canonical_label_unmatched_heading_returns_none() -> None:
    assert match_canonical_label("12 References") is None
    assert match_canonical_label("7 Safety monitoring") is None


def test_match_canonical_label_analysis_populations_does_not_false_positive() -> None:
    # Confirmed real heading from NCT04311632's SAP TOC: "population" as a
    # statistical-analysis-set term ("Safety Set", "Full Analysis Set"),
    # not a patient-eligibility heading -- \bpopulation\b correctly doesn't
    # match the plural "POPULATIONS" here, so this stays unclassified
    # rather than falsely landing in eligibility.
    assert match_canonical_label("7. ANALYSIS POPULATIONS") is None
    assert match_canonical_label("4 Population") == "eligibility"
    assert match_canonical_label("4 Study Population Selection") == "eligibility"


# --- _sections_from_markers -- pure page-range logic ----------------------


def test_sections_from_markers_empty_returns_empty() -> None:
    assert _sections_from_markers([], total_pages=10) == []


def test_sections_from_markers_prepends_leading_gap() -> None:
    markers = [(3, "synopsis", "Synopsis", "bookmark")]

    sections = _sections_from_markers(markers, total_pages=10)

    assert sections[0] == {
        "label": UNCLASSIFIED,
        "raw_heading_text": None,
        "page_range": [0, 2],
        "detection_method": "unmatched",
    }
    assert sections[1]["page_range"] == [3, 9]


def test_sections_from_markers_no_gap_when_first_marker_at_page_zero() -> None:
    markers = [(0, "synopsis", "Synopsis", "bookmark"), (5, None, "Weird Heading", "bookmark")]

    sections = _sections_from_markers(markers, total_pages=10)

    assert len(sections) == 2
    assert sections[0]["page_range"] == [0, 4]
    assert sections[1] == {
        "label": UNCLASSIFIED,
        "raw_heading_text": "Weird Heading",
        "page_range": [5, 9],
        "detection_method": "bookmark",
    }


# --- heading-candidate heuristics ------------------------------------------


def test_is_heading_candidate_bold_short_text() -> None:
    block = {"text": "3 Investigational Plan", "bold": True, "font_size": 12.0}
    assert _is_heading_candidate(block, body_font_size=12.0) is True


def test_is_heading_candidate_oversized_font() -> None:
    block = {"text": "STATISTICAL CONSIDERATIONS", "bold": False, "font_size": 16.0}
    assert _is_heading_candidate(block, body_font_size=12.0) is True


def test_is_heading_candidate_rejects_long_paragraph() -> None:
    long_text = " ".join(["word"] * 20)
    block = {"text": long_text, "bold": True, "font_size": 16.0}
    assert _is_heading_candidate(block, body_font_size=12.0) is False


def test_is_heading_candidate_rejects_plain_body_text() -> None:
    block = {"text": "Subjects will be randomized 1:1.", "bold": False, "font_size": 12.0}
    assert _is_heading_candidate(block, body_font_size=12.0) is False


def test_body_font_size_is_the_mode() -> None:
    document_content = {
        "pages": [
            {
                "blocks": [
                    {"font_size": 12.0},
                    {"font_size": 12.0},
                    {"font_size": 12.0},
                    {"font_size": 16.0},
                ]
            }
        ]
    }
    assert _body_font_size(document_content) == 12.0


def test_body_font_size_empty_document_returns_zero() -> None:
    assert _body_font_size({"pages": []}) == 0.0


# --- segment_document: bookmark path, real fixtures ------------------------


def _real_document_content(nct_id: str, pdf_path: Path) -> dict:
    import fitz

    doc = fitz.open(pdf_path)
    n_pages = doc.page_count
    doc.close()
    content = extract_document(pdf_path, ["born_digital"] * n_pages)
    content["nct_id"] = nct_id
    content["doc_type"] = "protocol"
    content["source_path"] = str(pdf_path)
    return content


def test_segment_document_novartis_bookmark_path() -> None:
    content = _real_document_content("NCT02798211", NOVARTIS_PDF)

    sections = segment_document(content, pdf_path=NOVARTIS_PDF)

    by_heading = {s["raw_heading_text"]: s for s in sections}
    assert by_heading["1 Introduction"]["label"] == "background"
    assert by_heading["2 Study objectives"]["label"] == "objectives"
    assert by_heading["3 Investigational plan"]["label"] == "study_design"
    assert by_heading["4 Population"]["label"] == "eligibility"
    assert by_heading["6 Visit schedule and assessments"]["label"] == "assessment_schedule"
    assert by_heading["9 Data analysis"]["label"] == "statistics"
    assert by_heading["10 Ethical considerations"]["label"] == "ethics"
    assert by_heading["7 Safety monitoring"]["label"] == UNCLASSIFIED  # real, not in taxonomy
    assert all(s["detection_method"] == "bookmark" for s in sections)


def test_segment_document_bms_bookmark_path_and_leading_gap() -> None:
    content = _real_document_content("NCT02872116", BMS_PDF)

    sections = segment_document(content, pdf_path=BMS_PDF)

    # BMS's first level-1 bookmark ("TITLE PAGE") starts at 0-indexed page 1,
    # not 0 -- confirms the leading-gap section is prepended for real data.
    assert sections[0] == {
        "label": UNCLASSIFIED,
        "raw_heading_text": None,
        "page_range": [0, 0],
        "detection_method": "unmatched",
    }
    by_heading = {s["raw_heading_text"]: s for s in sections}
    assert by_heading["SYNOPSIS"]["label"] == "synopsis"
    assert by_heading["1 INTRODUCTION AND STUDY RATIONALE"]["label"] == "background"
    assert by_heading["2 ETHICAL CONSIDERATIONS"]["label"] == "ethics"
    assert by_heading["3 INVESTIGATIONAL PLAN"]["label"] == "study_design"
    assert by_heading["5 STUDY ASSESSMENTS AND PROCEDURES"]["label"] == "assessment_schedule"
    assert by_heading["8 STATISTICAL CONSIDERATIONS"]["label"] == "statistics"
    assert by_heading["9 STUDY MANAGEMENT"]["label"] == "administrative"


def test_segment_document_capricor_no_ethics_but_has_administrative() -> None:
    content = _real_document_content("NCT02485938", CAPRICOR_PDF)

    sections = segment_document(content, pdf_path=CAPRICOR_PDF)

    # The confirmed real gap: no section anywhere in this document is
    # labeled "ethics" -- it genuinely isn't a distinct top-level heading
    # for this sponsor (corpus_assessment.md Sec.3).
    assert not any(s["label"] == "ethics" for s in sections)
    by_heading = {s["raw_heading_text"]: s for s in sections}
    assert by_heading["11 Administrative Considerations"]["label"] == "administrative"
    assert by_heading["8 Quality Control and Assurance"]["label"] == "administrative"
    assert by_heading["6 Study Procedures"]["label"] == "assessment_schedule"
    assert by_heading["7 Study Activities"]["label"] == "assessment_schedule"
    assert by_heading["4 Study Population Selection"]["label"] == "eligibility"


# --- segment_document: regex-scan fallback, real fixture (no bookmarks) ---


def test_segment_document_regex_fallback_on_bookmark_less_document() -> None:
    scanned_idx = {2, 3, 7, 8, 9, 10, 11, 22}
    page_classes = ["scanned" if i in scanned_idx else "born_digital" for i in range(29)]
    content = extract_document(NO_BOOKMARKS_PDF, page_classes)
    content["nct_id"] = "NCT04311632"
    content["doc_type"] = "sap"
    content["source_path"] = str(NO_BOOKMARKS_PDF)

    sections = segment_document(content, pdf_path=NO_BOOKMARKS_PDF)

    assert len(sections) > 1  # real headings found, not just the whole-doc fallback
    assert all(s["detection_method"] == "regex" for s in sections)
    statistics_sections = [s for s in sections if s["label"] == "statistics"]
    assert len(statistics_sections) >= 2
    assert any("Sample Size" in (s["raw_heading_text"] or "") for s in statistics_sections)


# --- segment_document: whole-document fallback ------------------------------


def test_segment_document_falls_back_to_whole_document_when_no_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import protocol_drift.ingestion.sections as sections_module

    monkeypatch.setattr(sections_module, "_level1_toc_markers", lambda pdf_path: [])

    content = {
        "nct_id": "NCT99999999",
        "doc_type": "protocol",
        "total_pages": 3,
        "source_path": str(tmp_path / "irrelevant.pdf"),
        "pages": [
            {
                "page_number": i,
                "blocks": [{"text": "plain body text.", "bold": False, "font_size": 12.0}],
            }
            for i in range(3)
        ],
    }

    sections = segment_document(content, pdf_path=Path("irrelevant.pdf"))

    assert sections == [
        {
            "label": UNCLASSIFIED,
            "raw_heading_text": None,
            "page_range": [0, 2],
            "detection_method": "unmatched",
        }
    ]


# --- segment_corpus orchestration ------------------------------------------


def _write_extracted(dest_dir: Path, nct_id: str, doc_type: str) -> None:
    (dest_dir / nct_id).mkdir(parents=True, exist_ok=True)
    (dest_dir / nct_id / f"{doc_type}.json").write_text(
        json.dumps(
            {
                "nct_id": nct_id,
                "doc_type": doc_type,
                "source_path": "irrelevant.pdf",
                "total_pages": 2,
                "pages": [{"page_number": 0, "blocks": []}, {"page_number": 1, "blocks": []}],
            }
        )
    )


def test_segment_corpus_counts_detected_and_fully_unclassified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import protocol_drift.ingestion.sections as sections_module

    def fake_segment_document(document_content: dict, pdf_path: Path | None = None) -> list[dict]:
        if document_content["nct_id"] == "NCT00000001":
            return [
                {
                    "label": "synopsis",
                    "raw_heading_text": "Synopsis",
                    "page_range": [0, 1],
                    "detection_method": "bookmark",
                }
            ]
        return [
            {
                "label": UNCLASSIFIED,
                "raw_heading_text": None,
                "page_range": [0, 1],
                "detection_method": "unmatched",
            }
        ]

    monkeypatch.setattr(sections_module, "segment_document", fake_segment_document)

    extracted_dir = tmp_path / "extracted"
    _write_extracted(extracted_dir, "NCT00000001", "protocol")
    _write_extracted(extracted_dir, "NCT00000002", "sap")
    dest_dir = tmp_path / "sections"

    result = segment_corpus(extracted_dir=extracted_dir, dest_dir=dest_dir)

    assert result["summary"]["documents"] == 2
    assert result["summary"]["detected"] == 1
    assert result["summary"]["fully_unclassified"] == 1
    assert result["fully_unclassified"] == [{"nct_id": "NCT00000002", "doc_type": "sap"}]
    assert (dest_dir / "NCT00000001" / "protocol.json").exists()
    assert (dest_dir / "NCT00000002" / "sap.json").exists()


def test_segment_corpus_logs_errors_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fitz

    import protocol_drift.ingestion.sections as sections_module

    def fake_segment_document(document_content: dict, pdf_path: Path | None = None) -> list[dict]:
        if document_content["nct_id"] == "NCT00000001":
            raise fitz.mupdf.FzErrorFormat("cannot find object in xref")
        return [
            {
                "label": "synopsis",
                "raw_heading_text": "Synopsis",
                "page_range": [0, 1],
                "detection_method": "bookmark",
            }
        ]

    monkeypatch.setattr(sections_module, "segment_document", fake_segment_document)

    extracted_dir = tmp_path / "extracted"
    _write_extracted(extracted_dir, "NCT00000001", "protocol")
    _write_extracted(extracted_dir, "NCT00000002", "sap")
    dest_dir = tmp_path / "sections"

    result = segment_corpus(extracted_dir=extracted_dir, dest_dir=dest_dir)

    assert result["summary"]["documents"] == 1
    assert result["summary"]["failed"] == 1
    error_log = (dest_dir / "segmentation_errors.log").read_text()
    assert "NCT00000001" in error_log


# --- detection_failures.log --------------------------------------------------


def test_write_detection_failures_log_uses_injected_sponsor_lookup(tmp_path: Path) -> None:
    fully_unclassified = [
        {"nct_id": "NCT00000001", "doc_type": "protocol"},
        {"nct_id": "NCT00000002", "doc_type": "sap"},
    ]

    def fake_lookup(nct_ids: list[str]) -> dict:
        assert sorted(nct_ids) == ["NCT00000001", "NCT00000002"]
        return {"NCT00000001": {"sponsor_name": "Acme Pharma", "sponsor_class": "INDUSTRY"}}

    dest_path = tmp_path / "detection_failures.log"
    write_detection_failures_log(
        fully_unclassified, dest_path=dest_path, sponsor_lookup=fake_lookup
    )

    lines = dest_path.read_text().splitlines()
    assert lines[0] == "NCT00000001\tprotocol\tAcme Pharma\tINDUSTRY"
    assert lines[1] == "NCT00000002\tsap\tUNKNOWN\tUNKNOWN"  # missing from lookup result


def test_write_detection_failures_log_empty_list_writes_empty_file(tmp_path: Path) -> None:
    dest_path = tmp_path / "detection_failures.log"

    write_detection_failures_log([], dest_path=dest_path, sponsor_lookup=lambda ids: {})

    assert dest_path.read_text() == ""


@pytest.mark.db
def test_lookup_sponsors_against_real_trials_table() -> None:
    # Seeded by S1-05's full-cohort load; NCT03007407 is a known real row.
    sponsors = lookup_sponsors(["NCT03007407"])

    assert sponsors["NCT03007407"]["sponsor_name"] == "NSABP Foundation Inc"
    assert sponsors["NCT03007407"]["sponsor_class"] == "NETWORK"


@pytest.mark.db
def test_lookup_sponsors_empty_input_returns_empty_without_querying() -> None:
    assert lookup_sponsors([]) == {}
