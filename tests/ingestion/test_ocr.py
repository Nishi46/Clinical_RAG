import json
import sys
import types
from pathlib import Path

import fitz
import pytest

from protocol_drift.ingestion.extract import extract_page
from protocol_drift.ingestion.ocr import ocr_page, pages_needing_ocr, write_ocr_backlog

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdfs"
MIXED_PDF = FIXTURES / "NCT04311632_sap.pdf"

# scratch/text_layer_results.json: NCT04311632 SAP's confirmed scanned page
MIXED_SCANNED_PAGE_INDEX = 2


def _fake_pytesseract(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Injects a fake pytesseract module into sys.modules -- works whether
    or not the real `ocr` extra is installed, since `import pytesseract`
    checks sys.modules first."""
    calls: list[tuple[str, str]] = []

    def image_to_string(image: object, lang: str = "eng") -> str:
        calls.append(("called", lang))
        return "OCR RESULT TEXT"

    fake_module = types.SimpleNamespace(image_to_string=image_to_string)
    monkeypatch.setitem(sys.modules, "pytesseract", fake_module)
    return calls


def _make_extracted_corpus(tmp_path: Path) -> Path:
    extracted_dir = tmp_path / "extracted"
    (extracted_dir / "NCT00000001").mkdir(parents=True)
    (extracted_dir / "NCT00000001" / "protocol.json").write_text(
        json.dumps(
            {
                "nct_id": "NCT00000001",
                "doc_type": "protocol",
                "source_path": "data/pdfs/NCT00000001/NCT00000001_protocol.pdf",
                "total_pages": 3,
                "pages": [
                    {
                        "page_number": 0,
                        "page_class": "born_digital",
                        "needs_ocr": False,
                        "blocks": [],
                    },
                    {"page_number": 1, "page_class": "scanned", "needs_ocr": True, "blocks": []},
                    {
                        "page_number": 2,
                        "page_class": "born_digital",
                        "needs_ocr": False,
                        "blocks": [],
                    },
                ],
            }
        )
    )
    (extracted_dir / "NCT00000002").mkdir(parents=True)
    (extracted_dir / "NCT00000002" / "sap.json").write_text(
        json.dumps(
            {
                "nct_id": "NCT00000002",
                "doc_type": "sap",
                "source_path": "data/pdfs/NCT00000002/NCT00000002_sap.pdf",
                "total_pages": 1,
                "pages": [
                    {
                        "page_number": 0,
                        "page_class": "born_digital",
                        "needs_ocr": False,
                        "blocks": [],
                    },
                ],
            }
        )
    )
    (extracted_dir / "extraction_errors.log").write_text("some prior failure\n")
    return extracted_dir


def test_pages_needing_ocr_finds_only_scanned_pages(tmp_path: Path) -> None:
    extracted_dir = _make_extracted_corpus(tmp_path)

    backlog = pages_needing_ocr(extracted_dir)

    assert len(backlog) == 1
    assert backlog[0]["nct_id"] == "NCT00000001"
    assert backlog[0]["doc_type"] == "protocol"
    assert backlog[0]["page_number"] == 1
    assert backlog[0]["source_path"] == "data/pdfs/NCT00000001/NCT00000001_protocol.pdf"
    assert "reason" in backlog[0]


def test_write_ocr_backlog_writes_json(tmp_path: Path) -> None:
    extracted_dir = _make_extracted_corpus(tmp_path)
    dest_path = tmp_path / "ocr_backlog.json"

    summary = write_ocr_backlog(extracted_dir=extracted_dir, dest_path=dest_path)

    assert summary == {"pages": 1}
    payload = json.loads(dest_path.read_text())
    assert len(payload["pages"]) == 1
    assert payload["pages"][0]["nct_id"] == "NCT00000001"


def test_ocr_page_renders_and_calls_pytesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_pytesseract(monkeypatch)

    doc = fitz.open(MIXED_PDF)
    try:
        page = doc[MIXED_SCANNED_PAGE_INDEX]
        text = ocr_page(page, lang="eng")
    finally:
        doc.close()

    assert text == "OCR RESULT TEXT"
    assert calls == [("called", "eng")]


def test_extract_page_default_path_never_touches_pytesseract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # sys.modules[name] = None forces `import pytesseract` to raise, exactly
    # as if the `ocr` extra were never installed -- proves the default
    # (with_ocr=False) path genuinely never imports it, independent of
    # whether it's actually installed in this environment.
    monkeypatch.setitem(sys.modules, "pytesseract", None)

    doc = fitz.open(MIXED_PDF)
    try:
        page = doc[MIXED_SCANNED_PAGE_INDEX]
        result = extract_page(page, "scanned", with_ocr=False)
    finally:
        doc.close()

    assert result["needs_ocr"] is True
    assert result["blocks"] == []
    assert result["ocr_applied"] is False


def test_extract_page_with_ocr_requires_pytesseract_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pytesseract", None)

    doc = fitz.open(MIXED_PDF)
    try:
        page = doc[MIXED_SCANNED_PAGE_INDEX]
        with pytest.raises(ImportError):
            extract_page(page, "scanned", with_ocr=True)
    finally:
        doc.close()


def test_extract_page_with_ocr_populates_blocks_only_for_scanned_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_pytesseract(monkeypatch)

    doc = fitz.open(MIXED_PDF)
    try:
        scanned_page = doc[MIXED_SCANNED_PAGE_INDEX]
        scanned_page_rect = list(scanned_page.rect)
        scanned_result = extract_page(scanned_page, "scanned", with_ocr=True)

        born_digital_page = doc[0]
        born_digital_result = extract_page(born_digital_page, "born_digital", with_ocr=True)
    finally:
        doc.close()

    assert scanned_result["needs_ocr"] is True
    assert scanned_result["ocr_applied"] is True
    assert scanned_result["blocks"] == [
        {"bbox": scanned_page_rect, "text": "OCR RESULT TEXT", "font_size": 0.0, "bold": False}
    ]
    assert calls == [("called", "eng")]  # OCR called exactly once, only for the scanned page

    # with_ocr=True must not change behavior for a page that already has a
    # real text layer -- OCR is only for pages S1-07 flagged scanned.
    assert born_digital_result["ocr_applied"] is False
    assert calls == [("called", "eng")]  # still just the one call
