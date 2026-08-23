"""Page classifier — born-digital vs. scanned, via the S0-03-refined heuristic.

Ports scratch/check_text_layer.py's logic exactly, not the naive version: a
page with little/no extractable text is only a real OCR candidate if it also
carries a raster image (a scanned photo of a page). No text + no image is
usually a blank/divider page; no text + only vector drawings is usually a
born-digital chart. Lumping either of those in with true scans overstated
S0-03's own scanned-page rate (9/34 mixed docs -> 4/34 once this refinement
was applied) — that overstatement is exactly the failure mode this module
must not repeat.

S0-03 ran this heuristic on a 20-trial / 34-document sample (no
therapeutic-area filter) and found 1.1% of pages scanned, 88% of documents
fully born-digital. This module re-measures both numbers on the real frozen
200-trial oncology cohort and flags if they diverge meaningfully from that
sample — the full-cohort number is what actually feeds the S1-G1 gate.
"""

from __future__ import annotations

import argparse
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import fitz  # pymupdf

PAGE_TEXT_THRESHOLD = 40  # chars; below this, a page has no usable text layer

DEFAULT_PDF_MANIFEST = Path("data/pdfs/manifest.json")
DEFAULT_CLASSIFICATION_PATH = Path("data/corpus_classification.json")

# S0-03 sample findings (scratch/corpus_assessment.md), for the divergence check.
S0_03_SAMPLE_PAGE_LEVEL_SCANNED_PCT = 1.1
S0_03_SAMPLE_BORN_DIGITAL_DOC_PCT = 88.0  # 30/34


class PageClass(StrEnum):
    BORN_DIGITAL = "born_digital"
    SCANNED = "scanned"
    BLANK_OR_VECTOR = "blank_or_vector"


def classify_page(page: fitz.Page) -> PageClass:
    text = page.get_text()
    if len(text.strip()) >= PAGE_TEXT_THRESHOLD:
        return PageClass.BORN_DIGITAL
    if page.get_images():
        return PageClass.SCANNED
    return PageClass.BLANK_OR_VECTOR


def classify_document(pdf_path: Path) -> dict[str, Any]:
    doc = fitz.open(pdf_path)
    try:
        page_classes = [classify_page(doc[i]) for i in range(doc.page_count)]
    finally:
        doc.close()

    total = len(page_classes)
    scanned = sum(1 for c in page_classes if c is PageClass.SCANNED)
    born_digital = sum(1 for c in page_classes if c is PageClass.BORN_DIGITAL)
    blank_or_vector = total - scanned - born_digital
    scanned_pct = scanned / total if total else 0.0

    if scanned_pct == 0:
        classification = "born_digital"
    elif scanned_pct == 1:
        classification = "scanned"
    else:
        classification = "mixed"

    return {
        "total_pages": total,
        "born_digital_pages": born_digital,
        "scanned_pages": scanned,
        "blank_or_vector_pages": blank_or_vector,
        "scanned_page_pct": round(scanned_pct * 100, 1),
        "classification": classification,
        "page_classes": [c.value for c in page_classes],
    }


def _divergence_flag(full_pct: float, sample_pct: float, label: str) -> str | None:
    """Flags if the full-cohort rate is more than 2x (or less than half) the
    S0-03 sample rate -- a signal worth a retro note, not necessarily a bug."""
    if sample_pct <= 0:
        return None
    ratio = full_pct / sample_pct
    if ratio > 2 or ratio < 0.5:
        return f"{label}: full-cohort {full_pct}% diverges >2x from S0-03 sample {sample_pct}%"
    return None


def classify_corpus(
    pdf_manifest_path: Path = DEFAULT_PDF_MANIFEST,
    dest_path: Path = DEFAULT_CLASSIFICATION_PATH,
) -> dict[str, Any]:
    manifest = json.loads(pdf_manifest_path.read_text())

    documents = []
    errors: list[str] = []
    for entry in manifest["entries"]:
        try:
            stats = classify_document(Path(entry["local_path"]))
        except (fitz.mupdf.FzErrorBase, RuntimeError, IndexError) as exc:
            # A real corpus has malformed PDFs (confirmed: NCT03081858's SAP
            # has a broken xref table). The same underlying corruption
            # surfaces as different exception types depending on access
            # pattern -- observed both FzErrorFormat (content stream
            # references a missing xref object) and IndexError (page_count
            # over-reports the actually-accessible page range) from this one
            # file. Log and keep going rather than abort a 300+ document
            # batch on one bad file.
            errors.append(f"{entry['nct_id']}\t{entry['doc_type']}\t{entry['local_path']}\t{exc}")
            continue
        documents.append({"nct_id": entry["nct_id"], "doc_type": entry["doc_type"], **stats})

    if errors:
        error_log = dest_path.parent / "corpus_classification_errors.log"
        error_log.parent.mkdir(parents=True, exist_ok=True)
        error_log.write_text("\n".join(errors) + "\n")

    total_pages = sum(d["total_pages"] for d in documents)
    total_scanned = sum(d["scanned_pages"] for d in documents)
    doc_level_counts: dict[str, int] = {}
    for d in documents:
        doc_level_counts[d["classification"]] = doc_level_counts.get(d["classification"], 0) + 1

    page_level_pct = round(100 * total_scanned / total_pages, 2) if total_pages else 0.0
    born_digital_doc_pct = (
        round(100 * doc_level_counts.get("born_digital", 0) / len(documents), 1)
        if documents
        else 0.0
    )

    summary = {
        "documents": len(documents),
        "failed_documents": len(errors),
        "total_pages": total_pages,
        "total_scanned_pages": total_scanned,
        "scanned_page_pct_page_level": page_level_pct,
        "born_digital_doc_pct": born_digital_doc_pct,
        "document_level_counts": doc_level_counts,
        "s0_03_comparison": {
            "sample_page_level_scanned_pct": S0_03_SAMPLE_PAGE_LEVEL_SCANNED_PCT,
            "sample_born_digital_doc_pct": S0_03_SAMPLE_BORN_DIGITAL_DOC_PCT,
            "page_level_flag": _divergence_flag(
                page_level_pct, S0_03_SAMPLE_PAGE_LEVEL_SCANNED_PCT, "page-level scanned rate"
            ),
            "born_digital_doc_flag": _divergence_flag(
                born_digital_doc_pct,
                S0_03_SAMPLE_BORN_DIGITAL_DOC_PCT,
                "born-digital document rate",
            ),
        },
    }

    payload = {"summary": summary, "documents": documents}
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"Classified {len(documents)} documents, {total_pages} pages")
    if errors:
        print(f"{len(errors)} document(s) failed to classify (malformed PDF) -- see {error_log}")
    sample_pct = S0_03_SAMPLE_PAGE_LEVEL_SCANNED_PCT
    print(f"Page-level scanned rate: {page_level_pct}% (S0-03 sample: {sample_pct}%)")
    for k, v in sorted(doc_level_counts.items()):
        print(f"  {k}: {v}/{len(documents)}")
    for flag in (
        summary["s0_03_comparison"]["page_level_flag"],
        summary["s0_03_comparison"]["born_digital_doc_flag"],
    ):
        if flag:
            print(f"NOTE: {flag}")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify every PDF page as born-digital or scanned."
    )
    parser.add_argument("--pdf-manifest", type=Path, default=DEFAULT_PDF_MANIFEST)
    parser.add_argument("--dest", type=Path, default=DEFAULT_CLASSIFICATION_PATH)
    args = parser.parse_args()

    classify_corpus(pdf_manifest_path=args.pdf_manifest, dest_path=args.dest)


if __name__ == "__main__":
    main()
