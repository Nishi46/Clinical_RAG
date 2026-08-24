import json
from pathlib import Path

import fitz
import pytest

from protocol_drift.ingestion.extract import (
    document_pdfs,
    extract_corpus,
    extract_document,
    extract_page,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdfs"
BORN_DIGITAL_PDF = FIXTURES / "NCT02798211_protocol.pdf"
MIXED_PDF = FIXTURES / "NCT04311632_sap.pdf"
REDACTED_PDF = FIXTURES / "NCT02872116_protocol.pdf"

# scratch/text_layer_results.json: NCT04311632 SAP's confirmed scanned pages
MIXED_SCANNED_PAGE_INDEX = 2
# corpus_assessment.md Sec.6/Sec.8: the one confirmed real redaction
REDACTED_PAGE_INDEX = 84
UNREDACTED_PAGE_INDEX = 0


def _manifest_and_classification(tmp_path: Path) -> tuple[Path, Path]:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "nct_id": "NCT02798211",
                        "doc_type": "protocol",
                        "local_path": str(BORN_DIGITAL_PDF),
                        "sha256": "abc123",
                    },
                    {
                        "nct_id": "NCT02798211",
                        "doc_type": "icf",
                        "local_path": "data/pdfs/NCT02798211/NCT02798211_icf.pdf",
                        "sha256": "def456",
                    },
                    {
                        "nct_id": "NCT03081858",
                        "doc_type": "sap",
                        "local_path": "data/pdfs/NCT03081858/broken_sap.pdf",
                        "sha256": "ghi789",
                    },
                ]
            }
        )
    )
    classification_path = tmp_path / "corpus_classification.json"
    classification_path.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "nct_id": "NCT02798211",
                        "doc_type": "protocol",
                        "page_classes": ["born_digital"] * 94,
                    }
                    # NCT03081858 intentionally absent -- the known
                    # malformed PDF that S1-07 failed to classify.
                ]
            }
        )
    )
    return manifest_path, classification_path


def test_document_pdfs_excludes_icf_and_unclassified(tmp_path: Path) -> None:
    manifest_path, classification_path = _manifest_and_classification(tmp_path)

    entries = document_pdfs(manifest_path, classification_path)

    assert len(entries) == 1
    assert entries[0]["nct_id"] == "NCT02798211"
    assert entries[0]["doc_type"] == "protocol"
    assert entries[0]["page_classes"] == ["born_digital"] * 94


def _duplicate_doc_type_manifest_and_classification(tmp_path: Path) -> tuple[Path, Path]:
    # Confirmed real case: NCT03083873 has 4 "protocol" PDFs, NCT03043313 has
    # 2 "sap" PDFs -- amendment resubmissions under the same doc_type.
    # classify.py's output has no filename, only nct_id+doc_type, so pairing
    # must preserve manifest order rather than collapse to one entry, and
    # the two PDFs must land at distinct destination paths. Mirror S1-06's
    # real naming convention (local_path stem = "{nct_id}_{doc_type}[_N]")
    # by copying the fixtures under correctly prefixed names, since
    # _dest_stem derives the output filename from that stem.
    first_path = tmp_path / "NCT99999999_sap.pdf"
    second_path = tmp_path / "NCT99999999_sap_2.pdf"
    first_path.write_bytes(BORN_DIGITAL_PDF.read_bytes())  # 94 pages
    second_path.write_bytes(MIXED_PDF.read_bytes())  # 29 pages

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "nct_id": "NCT99999999",
                        "doc_type": "sap",
                        "local_path": str(first_path),
                        "sha256": "first",
                    },
                    {
                        "nct_id": "NCT99999999",
                        "doc_type": "sap",
                        "local_path": str(second_path),
                        "sha256": "second",
                    },
                ]
            }
        )
    )
    classification_path = tmp_path / "corpus_classification.json"
    classification_path.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "nct_id": "NCT99999999",
                        "doc_type": "sap",
                        "page_classes": ["born_digital"] * 94,
                    },
                    {
                        "nct_id": "NCT99999999",
                        "doc_type": "sap",
                        "page_classes": ["born_digital"] * 29,
                    },
                ]
            }
        )
    )
    return manifest_path, classification_path


def test_document_pdfs_pairs_duplicate_doc_type_in_order(tmp_path: Path) -> None:
    manifest_path, classification_path = _duplicate_doc_type_manifest_and_classification(tmp_path)

    entries = document_pdfs(manifest_path, classification_path)

    assert len(entries) == 2
    assert len(entries[0]["page_classes"]) == 94
    assert len(entries[1]["page_classes"]) == 29


def test_extract_corpus_disambiguates_duplicate_doc_type_paths(tmp_path: Path) -> None:
    manifest_path, classification_path = _duplicate_doc_type_manifest_and_classification(tmp_path)
    dest_dir = tmp_path / "extracted"

    summary = extract_corpus(
        pdf_manifest_path=manifest_path, classification_path=classification_path, dest_dir=dest_dir
    )

    assert summary["extracted"] == 2
    assert summary["failed"] == 0
    first = json.loads((dest_dir / "NCT99999999" / "sap.json").read_text())
    second = json.loads((dest_dir / "NCT99999999" / "sap_2.json").read_text())
    assert first["total_pages"] == 94
    assert second["total_pages"] == 29


def test_extract_page_scanned_page_skips_content() -> None:
    doc = fitz.open(MIXED_PDF)
    try:
        page = doc[MIXED_SCANNED_PAGE_INDEX]
        result = extract_page(page, "scanned")
    finally:
        doc.close()

    assert result["needs_ocr"] is True
    assert result["blocks"] == []
    assert result["has_redaction"] is False
    assert result["page_number"] == MIXED_SCANNED_PAGE_INDEX


def test_extract_page_born_digital_preserves_reading_order() -> None:
    doc = fitz.open(BORN_DIGITAL_PDF)
    try:
        page = doc[0]
        result = extract_page(page, "born_digital")
    finally:
        doc.close()

    assert result["needs_ocr"] is False
    blocks = result["blocks"]
    assert len(blocks) > 1
    for block in blocks:
        assert block["text"]
        assert len(block["bbox"]) == 4
        assert block["font_size"] > 0
        assert isinstance(block["bold"], bool)

    # top-to-bottom reading order: block top-edges (bbox[1] == y0) must be
    # non-decreasing, modulo blocks that sit side-by-side on the same line.
    tops = [b["bbox"][1] for b in blocks]
    out_of_order = [i for i in range(len(tops) - 1) if tops[i] - tops[i + 1] > 5.0]
    assert out_of_order == []


def test_extract_page_detects_known_redaction() -> None:
    doc = fitz.open(REDACTED_PDF)
    try:
        redacted = extract_page(doc[REDACTED_PAGE_INDEX], "born_digital")
        clean = extract_page(doc[UNREDACTED_PAGE_INDEX], "born_digital")
    finally:
        doc.close()

    assert redacted["has_redaction"] is True
    assert clean["has_redaction"] is False


def test_extract_document_full_pdf() -> None:
    doc = fitz.open(BORN_DIGITAL_PDF)
    page_count = doc.page_count
    doc.close()

    result = extract_document(BORN_DIGITAL_PDF, ["born_digital"] * page_count)

    assert result["total_pages"] == page_count
    assert len(result["pages"]) == page_count
    assert result["pages"][0]["needs_ocr"] is False


def test_extract_document_page_count_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="page count"):
        extract_document(BORN_DIGITAL_PDF, ["born_digital"] * 3)


def test_extract_corpus_writes_files_and_excludes_icf(tmp_path: Path) -> None:
    manifest_path, classification_path = _manifest_and_classification(tmp_path)
    dest_dir = tmp_path / "extracted"

    summary = extract_corpus(
        pdf_manifest_path=manifest_path, classification_path=classification_path, dest_dir=dest_dir
    )

    assert summary["extracted"] == 1
    assert summary["skipped"] == 0
    assert summary["failed"] == 0

    out_path = dest_dir / "NCT02798211" / "protocol.json"
    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload["nct_id"] == "NCT02798211"
    assert payload["doc_type"] == "protocol"
    assert payload["total_pages"] == 94
    assert not (dest_dir / "NCT02798211" / "icf.json").exists()


def test_extract_corpus_skips_unchanged_on_rerun(tmp_path: Path) -> None:
    manifest_path, classification_path = _manifest_and_classification(tmp_path)
    dest_dir = tmp_path / "extracted"

    first = extract_corpus(
        pdf_manifest_path=manifest_path, classification_path=classification_path, dest_dir=dest_dir
    )
    second = extract_corpus(
        pdf_manifest_path=manifest_path, classification_path=classification_path, dest_dir=dest_dir
    )

    assert first["extracted"] == 1
    assert second["extracted"] == 0
    assert second["skipped"] == 1


def test_extract_corpus_logs_malformed_pdf_and_continues(tmp_path: Path) -> None:
    # give the "broken" doc a real, openable PDF so extract_document reaches
    # the page-count mismatch check rather than failing to even open the
    # file -- exercises the logged-error path without needing an
    # actually-corrupt fixture (that case is already covered directly by
    # test_extract_document_page_count_mismatch_raises).
    broken_path = tmp_path / "broken_sap.pdf"
    broken_path.write_bytes(MIXED_PDF.read_bytes())

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "nct_id": "NCT02798211",
                        "doc_type": "protocol",
                        "local_path": str(BORN_DIGITAL_PDF),
                        "sha256": "abc123",
                    },
                    {
                        "nct_id": "NCT03081858",
                        "doc_type": "sap",
                        "local_path": str(broken_path),
                        "sha256": "ghi789",
                    },
                ]
            }
        )
    )
    classification_path = tmp_path / "corpus_classification.json"
    classification_path.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "nct_id": "NCT02798211",
                        "doc_type": "protocol",
                        "page_classes": ["born_digital"] * 94,
                    },
                    {
                        "nct_id": "NCT03081858",
                        "doc_type": "sap",
                        "page_classes": ["born_digital"] * 999999,
                    },
                ]
            }
        )
    )
    dest_dir = tmp_path / "extracted"

    summary = extract_corpus(
        pdf_manifest_path=manifest_path,
        classification_path=classification_path,
        dest_dir=dest_dir,
    )

    assert summary["extracted"] == 1
    assert summary["failed"] == 1
    error_log = dest_dir / "extraction_errors.log"
    assert "NCT03081858" in error_log.read_text()
