"""OCR fallback for scanned pages -- deliberately scoped down.

The corpus's measured page-level scanned rate is 2.69% (docs/corpus.md),
comfortably under the S1-G1 "<15%" threshold ("Proceed. OCR is a
footnote.") and matching the project's own cut list ("S2-03 OCR fallback --
if scan rate is low, just exclude those pages and report it"). The default
ingestion path therefore treats a needs_ocr page as absent content: this
module's default entrypoint just enumerates the backlog for the record.
Real OCR runs only behind extract.py's explicit --with-ocr flag, for a
one-time spot-check -- not a batch job over the ~500-page backlog.

pytesseract is imported lazily inside ocr_page(), not at module level, so
importing this module -- e.g. just to build the backlog -- never requires
the optional `ocr` dependency group to be installed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz  # pymupdf

DEFAULT_EXTRACTED_DIR = Path("data/extracted")
DEFAULT_BACKLOG_PATH = Path("data/ocr_backlog.json")

SCANNED_PAGE_REASON = "classified scanned in S1-07: no extractable text layer, has a raster image"


def pages_needing_ocr(extracted_dir: Path = DEFAULT_EXTRACTED_DIR) -> list[dict[str, Any]]:
    """Every page S2-01 marked needs_ocr=True, across the extracted corpus."""
    backlog = []
    for path in sorted(extracted_dir.glob("*/*.json")):
        document = json.loads(path.read_text())
        for page in document["pages"]:
            if page["needs_ocr"]:
                backlog.append(
                    {
                        "nct_id": document["nct_id"],
                        "doc_type": document["doc_type"],
                        "source_path": document["source_path"],
                        "page_number": page["page_number"],
                        "reason": SCANNED_PAGE_REASON,
                    }
                )
    return backlog


def write_ocr_backlog(
    extracted_dir: Path = DEFAULT_EXTRACTED_DIR,
    dest_path: Path = DEFAULT_BACKLOG_PATH,
) -> dict[str, Any]:
    backlog = pages_needing_ocr(extracted_dir)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps({"pages": backlog}, indent=2) + "\n")
    print(f"{len(backlog)} page(s) need OCR -> {dest_path}")
    return {"pages": len(backlog)}


def ocr_page(page: fitz.Page, lang: str = "eng", dpi: int = 300) -> str:
    """Render one page to an image and run local Tesseract over it.

    Only ever called for a page already flagged needs_ocr -- never on a
    page with a real text layer, and never at all unless --with-ocr is
    passed explicitly.
    """
    import io

    import pytesseract
    from PIL import Image

    pixmap = page.get_pixmap(dpi=dpi)
    image = Image.open(io.BytesIO(pixmap.tobytes("png")))
    return str(pytesseract.image_to_string(image, lang=lang))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enumerate pages needing OCR across the extracted corpus."
    )
    parser.add_argument("--extracted-dir", type=Path, default=DEFAULT_EXTRACTED_DIR)
    parser.add_argument("--dest", type=Path, default=DEFAULT_BACKLOG_PATH)
    args = parser.parse_args()

    write_ocr_backlog(extracted_dir=args.extracted_dir, dest_path=args.dest)


if __name__ == "__main__":
    main()
