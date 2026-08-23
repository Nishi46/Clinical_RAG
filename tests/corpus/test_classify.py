import json
from pathlib import Path

import fitz
import pytest

from protocol_drift.corpus.classify import (
    PageClass,
    classify_corpus,
    classify_document,
    classify_page,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdfs"
BORN_DIGITAL_PDF = FIXTURES / "NCT02798211_protocol.pdf"
MIXED_PDF = FIXTURES / "NCT04311632_sap.pdf"


def test_classify_page_born_digital() -> None:
    doc = fitz.open(BORN_DIGITAL_PDF)
    try:
        assert classify_page(doc[0]) is PageClass.BORN_DIGITAL
    finally:
        doc.close()


def test_classify_document_born_digital_pdf() -> None:
    # confirmed real value from S0-03's text_layer_results.json
    result = classify_document(BORN_DIGITAL_PDF)

    assert result["total_pages"] == 94
    assert result["scanned_pages"] == 0
    assert result["classification"] == "born_digital"
    assert result["scanned_page_pct"] == 0.0


def test_classify_document_mixed_pdf() -> None:
    # confirmed real value from S0-03's text_layer_results.json: 29 pages,
    # 27.6% scanned, "mixed" -- this is the real scanned-insert case S0-03
    # spot-checked by hand.
    result = classify_document(MIXED_PDF)

    assert result["total_pages"] == 29
    assert result["classification"] == "mixed"
    assert result["scanned_page_pct"] == pytest.approx(27.6, abs=0.1)
    assert result["scanned_pages"] > 0
    assert result["born_digital_pages"] > 0
    assert len(result["page_classes"]) == 29


def test_classify_corpus_writes_summary_and_documents(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "nct_id": "NCT02798211",
                        "doc_type": "protocol",
                        "local_path": str(BORN_DIGITAL_PDF),
                    },
                    {
                        "nct_id": "NCT04311632",
                        "doc_type": "sap",
                        "local_path": str(MIXED_PDF),
                    },
                ]
            }
        )
    )
    dest = tmp_path / "corpus_classification.json"

    summary = classify_corpus(pdf_manifest_path=manifest_path, dest_path=dest)

    assert summary["documents"] == 2
    assert summary["total_pages"] == 94 + 29
    assert summary["document_level_counts"]["born_digital"] == 1
    assert summary["document_level_counts"]["mixed"] == 1

    payload = json.loads(dest.read_text())
    assert payload["summary"] == summary
    assert len(payload["documents"]) == 2
    nct_ids = {d["nct_id"] for d in payload["documents"]}
    assert nct_ids == {"NCT02798211", "NCT04311632"}


def test_classify_corpus_logs_malformed_pdf_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Confirmed against the real cohort: NCT03081858's SAP has a broken xref
    # table (fitz.mupdf.FzErrorFormat: "cannot find object in xref"). A
    # single malformed document must not abort the whole batch -- simulate
    # it via monkeypatch since reproducing MuPDF's exact xref-corruption
    # error in a small synthetic fixture isn't reliable.
    import protocol_drift.corpus.classify as classify_module

    real_classify_document = classify_module.classify_document

    def _classify_document(pdf_path: Path) -> dict:
        if "broken" in str(pdf_path):
            raise fitz.mupdf.FzErrorFormat("cannot find object in xref (20703 0 R)")
        return real_classify_document(pdf_path)

    monkeypatch.setattr(classify_module, "classify_document", _classify_document)

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "nct_id": "NCT02798211",
                        "doc_type": "protocol",
                        "local_path": str(BORN_DIGITAL_PDF),
                    },
                    {
                        "nct_id": "NCT03081858",
                        "doc_type": "sap",
                        "local_path": "data/pdfs/NCT03081858/broken_sap.pdf",
                    },
                ]
            }
        )
    )
    dest = tmp_path / "corpus_classification.json"

    summary = classify_module.classify_corpus(pdf_manifest_path=manifest_path, dest_path=dest)

    assert summary["documents"] == 1
    assert summary["failed_documents"] == 1
    error_log = tmp_path / "corpus_classification_errors.log"
    assert "NCT03081858" in error_log.read_text()


def test_divergence_flag_present_when_full_cohort_rate_far_from_sample(tmp_path: Path) -> None:
    # both fixture docs are scan-heavier than S0-03's 1.1% sample average
    # once combined -- exercise the flag path, not just the happy path.
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "entries": [
                    {"nct_id": "NCT04311632", "doc_type": "sap", "local_path": str(MIXED_PDF)}
                ]
            }
        )
    )
    dest = tmp_path / "corpus_classification.json"

    summary = classify_corpus(pdf_manifest_path=manifest_path, dest_path=dest)

    assert summary["s0_03_comparison"]["page_level_flag"] is not None
