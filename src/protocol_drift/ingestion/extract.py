"""Text extraction pipeline -- layout-aware PDF -> ordered page/block content.

Reads only from data/pdfs/manifest.json (S1-06's frozen download) and
data/corpus_classification.json (S1-07's per-page classification), never
re-downloads or re-classifies. ICF documents are excluded here (per
scratch/pdf_notes.md: ICFs describe participant consent, not trial design or
outcomes -- they never enter the ingestion corpus).

A page S1-07 already classified SCANNED is not re-extracted as garbled text;
it's marked needs_ocr=True with no blocks. That matches S2-03's default
(skip + report, given the corpus's measured 2.69% page-level scanned rate)
rather than pulling in near-empty or garbled "content" from a page with no
real text layer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import fitz  # pymupdf

from protocol_drift.ingestion.ocr import ocr_page

logger = logging.getLogger(__name__)

DEFAULT_PDF_MANIFEST = Path("data/pdfs/manifest.json")
DEFAULT_CLASSIFICATION_PATH = Path("data/corpus_classification.json")
DEFAULT_DEST_DIR = Path("data/extracted")

INGESTIBLE_DOC_TYPES = {"protocol", "sap"}

BOLD_FLAG = 1 << 4  # PyMuPDF span flags bit 4 = bold

# Redaction candidate heuristic -- calibrated against the one confirmed real
# redaction in the corpus (NCT02872116 protocol, page index 84, table
# 5.1-2's "Collection of biomarker sampling" row; corpus_assessment.md
# Sec.6/Sec.8). An initial page-area-fraction-only heuristic was tried and
# rejected: the true redaction covers only ~0.57% of the page, and a plain
# "solid near-black fill" filter flags something on nearly every page (dark
# table rules, header/footer bars) since fills that small are common page
# furniture, not just redactions. Absolute size (a redaction blacks out
# roughly one table row of text) plus excluding the header/footer margin
# band cuts this from ~1 hit/page down to ~1 hit per 7-8 pages. This is
# still a coarse *candidate* flag, not a certainty signal -- expect some
# false positives (shaded emphasis boxes) and don't treat has_redaction as
# ground truth without a look.
REDACTION_MIN_WIDTH_PT = 20.0
REDACTION_MIN_HEIGHT_PT = 6.0
REDACTION_MAX_HEIGHT_PT = 40.0
REDACTION_MAX_FILL_BRIGHTNESS = 0.25
REDACTION_MARGIN_FRACTION = 0.10  # exclude running header/footer furniture


def document_pdfs(
    pdf_manifest_path: Path = DEFAULT_PDF_MANIFEST,
    classification_path: Path = DEFAULT_CLASSIFICATION_PATH,
) -> list[dict[str, Any]]:
    """Non-ICF cohort documents, joined against their per-page classification.

    A manifest entry with no matching classification (the one malformed PDF
    S1-07 logged and skipped, e.g.) is silently excluded -- there's nothing
    to extract without a page-class list to key off of.

    A handful of trials have more than one document of the same doc_type
    (confirmed: NCT03083873 has 4 "protocol" PDFs, NCT03043313 has 2 "sap"
    PDFs -- amendment resubmissions under the same category). classify.py's
    output has no filename to disambiguate them, only nct_id+doc_type, so a
    plain dict keyed on (nct_id, doc_type) would silently collapse duplicates
    to whichever was classified last. Instead, consume classification
    entries in the same order classify.py produced them (it iterates
    manifest["entries"] in order, appending one classification per success),
    so a doc_type with N manifest entries for one trial gets paired with its
    N classification entries in the same relative order. This breaks only if
    a classification failure and a same-doc_type duplicate coincide for one
    trial -- confirmed not the case in the current corpus (the one logged
    classification failure, NCT03081858's sap, has no duplicate).
    """
    manifest = json.loads(pdf_manifest_path.read_text())
    classification = json.loads(classification_path.read_text())

    page_classes_by_doc: dict[tuple[str, str], deque[list[str]]] = defaultdict(deque)
    for d in classification["documents"]:
        page_classes_by_doc[(d["nct_id"], d["doc_type"])].append(d["page_classes"])

    entries = []
    for entry in manifest["entries"]:
        if entry["doc_type"] not in INGESTIBLE_DOC_TYPES:
            continue
        queue = page_classes_by_doc.get((entry["nct_id"], entry["doc_type"]))
        if not queue:
            continue
        entries.append({**entry, "page_classes": queue.popleft()})
    return entries


def _is_redaction_candidate(drawing: dict[str, Any], page_height: float) -> bool:
    fill = drawing.get("fill")
    rect = drawing.get("rect")
    if fill is None or rect is None:
        return False
    if max(fill) > REDACTION_MAX_FILL_BRIGHTNESS:
        return False
    if not (REDACTION_MIN_HEIGHT_PT <= rect.height <= REDACTION_MAX_HEIGHT_PT):
        return False
    if rect.width < REDACTION_MIN_WIDTH_PT:
        return False
    margin = REDACTION_MARGIN_FRACTION * page_height
    if rect.y0 < margin or rect.y1 > page_height - margin:
        return False
    return True


def _extract_block(raw_block: dict[str, Any]) -> dict[str, Any] | None:
    if raw_block.get("type") != 0:  # 0 = text block, 1 = image block
        return None
    spans = [span for line in raw_block["lines"] for span in line["spans"]]
    text = "".join(span["text"] for span in spans).strip()
    if not text:
        return None
    return {
        "bbox": list(raw_block["bbox"]),
        "text": text,
        "font_size": max((span["size"] for span in spans), default=0.0),
        "bold": any(span["flags"] & BOLD_FLAG for span in spans),
    }


def extract_page(page: fitz.Page, page_class: str, with_ocr: bool = False) -> dict[str, Any]:
    """Layout-aware extraction of one page, respecting S1-07's classification.

    A SCANNED page is never sent through get_text() -- it has no real text
    layer, so anything extracted would be empty or garbled rather than
    content. By default it's left empty (needs_ocr=True, blocks=[]) per
    S2-03's scoped-down default (skip + report, given the corpus's measured
    2.69% page-level scanned rate). Only when with_ocr=True is a scanned
    page actually sent through Tesseract -- a one-time spot-check path, not
    the default pipeline behavior.
    """
    if page_class == "scanned":
        blocks: list[dict[str, Any]] = []
        ocr_applied = False
        if with_ocr:
            text = ocr_page(page).strip()
            if text:
                blocks = [{"bbox": list(page.rect), "text": text, "font_size": 0.0, "bold": False}]
            ocr_applied = True
        return {
            "page_number": page.number,
            "page_class": page_class,
            "needs_ocr": True,
            "has_redaction": False,
            "blocks": blocks,
            "ocr_applied": ocr_applied,
        }

    raw = page.get_text("dict", sort=True)
    blocks = []
    for raw_block in raw["blocks"]:
        block = _extract_block(raw_block)
        if block is not None:
            blocks.append(block)

    has_redaction = any(_is_redaction_candidate(d, page.rect.height) for d in page.get_drawings())

    return {
        "page_number": page.number,
        "page_class": page_class,
        "needs_ocr": False,
        "has_redaction": has_redaction,
        "blocks": blocks,
        "ocr_applied": False,
    }


def extract_document(
    pdf_path: Path, page_classes: list[str], with_ocr: bool = False
) -> dict[str, Any]:
    doc = fitz.open(pdf_path)
    try:
        if doc.page_count != len(page_classes):
            raise ValueError(
                f"{pdf_path}: page count {doc.page_count} != {len(page_classes)} classified pages"
            )
        pages = [
            extract_page(doc[i], page_classes[i], with_ocr=with_ocr) for i in range(doc.page_count)
        ]
    finally:
        doc.close()
    return {"total_pages": len(pages), "pages": pages}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dest_stem(entry: dict[str, Any]) -> str:
    """Filename stem for this document's output, disambiguating trials with
    more than one document of the same doc_type (e.g. NCT03083873's 4
    protocol PDFs -> local_path stems "..._protocol", "..._protocol_2", ...
    per S1-06's download.py naming). Falls back to the bare doc_type if
    local_path doesn't follow that convention."""
    local_stem = Path(entry["local_path"]).stem
    prefix = f"{entry['nct_id']}_"
    if local_stem.startswith(prefix):
        return local_stem[len(prefix) :]
    return str(entry["doc_type"])


def extract_corpus(
    pdf_manifest_path: Path = DEFAULT_PDF_MANIFEST,
    classification_path: Path = DEFAULT_CLASSIFICATION_PATH,
    dest_dir: Path = DEFAULT_DEST_DIR,
    force: bool = False,
    with_ocr: bool = False,
) -> dict[str, Any]:
    entries = document_pdfs(pdf_manifest_path, classification_path)

    extracted = 0
    skipped = 0
    errors: list[str] = []
    for entry in entries:
        pdf_path = Path(entry["local_path"])
        dest_path = dest_dir / entry["nct_id"] / f"{_dest_stem(entry)}.json"
        source_sha256 = entry["sha256"]

        if not force and dest_path.exists():
            existing = json.loads(dest_path.read_text())
            # with_ocr must also match the cached run -- otherwise a stale
            # cache from a plain extraction would silently look "resumed"
            # even though a scanned page never actually went through OCR.
            if (
                existing.get("source_sha256") == source_sha256
                and existing.get("with_ocr", False) == with_ocr
            ):
                skipped += 1
                continue

        try:
            content = extract_document(pdf_path, entry["page_classes"], with_ocr=with_ocr)
        except (fitz.mupdf.FzErrorBase, RuntimeError, IndexError, ValueError) as exc:
            # Same malformed-PDF failure modes S1-07 already saw (e.g.
            # NCT03081858's broken xref table) -- log and keep going rather
            # than abort a multi-hundred-document batch on one bad file.
            errors.append(f"{entry['nct_id']}\t{entry['doc_type']}\t{pdf_path}\t{exc}")
            continue

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "nct_id": entry["nct_id"],
            "doc_type": entry["doc_type"],
            "source_path": str(pdf_path),
            "source_sha256": source_sha256,
            "with_ocr": with_ocr,
            **content,
        }
        dest_path.write_text(json.dumps(payload, indent=2) + "\n")
        extracted += 1

    if errors:
        dest_dir.mkdir(parents=True, exist_ok=True)
        error_log = dest_dir / "extraction_errors.log"
        error_log.write_text("\n".join(errors) + "\n")

    summary = {
        "documents": len(entries),
        "extracted": extracted,
        "skipped": skipped,
        "failed": len(errors),
    }
    print(f"Extracted {extracted} document(s), skipped {skipped}, {len(errors)} failed")
    if errors:
        error_log_path = dest_dir / "extraction_errors.log"
        print(f"{len(errors)} document(s) failed to extract -- see {error_log_path}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Layout-aware text extraction for the frozen protocol/SAP corpus."
    )
    parser.add_argument("--pdf-manifest", type=Path, default=DEFAULT_PDF_MANIFEST)
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION_PATH)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_DIR)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--with-ocr",
        action="store_true",
        help="Run scanned pages through local Tesseract (requires the 'ocr' extra). "
        "A one-time spot-check per S2-03, not intended for the full backlog.",
    )
    args = parser.parse_args()

    extract_corpus(
        pdf_manifest_path=args.pdf_manifest,
        classification_path=args.classification,
        dest_dir=args.dest,
        force=args.force,
        with_ocr=args.with_ocr,
    )


if __name__ == "__main__":
    main()
