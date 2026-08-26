"""Table extraction with header propagation.

Uses PyMuPDF's built-in `page.find_tables()` -- pymupdf is already a pinned
dependency, so this needs no new heavy table-extraction library (Camelot
needs Ghostscript; pdfplumber is a separate parser entirely).

Ground-truthed against the one confirmed real multi-page assessment table
in the corpus, NCT02872116 protocol Table 5.1-2/5.1-3
(corpus_assessment.md Sec.6). Geometric cell-bbox analysis -- not
text-pattern guessing -- found the table's actual merges are: (a)
full-width single-cell "section divider" rows ("PK and Immunogenicity
Sampling" etc., colspan=5), (b) a colspan=4 merge for "See Table 5.5-1 for
details..." reference cells, (c) the confirmed redacted "Collection of
biomarker sampling" row (colspan=4, empty text -- the redaction itself),
and (d) a genuine rowspan=2 merge for "See Note" reference cells spanning
the FACT-Ga/EQ-5D-3L rows. Two side-by-side "See Note" cells that *look*
merged on the rendered page turned out, on inspection of the real cell
bboxes, to be two separate unmerged cells with duplicate literal text --
confirmed by geometry, not assumed from how the page looks.

Reads only from data/extracted/ (S2-01, for block text used in the
fallback caption search) and data/sections/ (S2-04, for source_section),
plus the original PDFs (find_tables() needs the live page object).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import fitz  # pymupdf

from protocol_drift.ingestion.sections import UNCLASSIFIED

DEFAULT_EXTRACTED_DIR = Path("data/extracted")
DEFAULT_SECTIONS_DIR = Path("data/sections")
DEFAULT_DEST_DIR = Path("data/tables")

CAPTION_PATTERN = re.compile(r"^(table|figure)\s+\d", re.IGNORECASE)
# Trailing parenthetical that looks unit-like (no spaces, no "see ..."
# cross-reference text) -- e.g. "Weight (kg)" -> unit "kg", but "(see
# Section 4.5.1.6)" or "(CA209649)" (a protocol code, confirmed real in
# this table's own caption) correctly don't match.
UNIT_PATTERN = re.compile(r"\(([A-Za-z0-9/%°µ]{1,10})\)\s*$")

PAGE_ERROR_TYPES = (fitz.mupdf.FzErrorBase, RuntimeError, IndexError, ValueError)


def _row_text_count(row: list[str | None]) -> int:
    return sum(1 for v in row if v is not None)


def _parse_header(text: str) -> dict[str, Any]:
    match = UNIT_PATTERN.search(text)
    return {"text": text, "unit": match.group(1) if match else None}


def _span_lookup(
    cells: list[tuple[float, float, float, float]],
) -> dict[tuple[int, int], tuple[int, int]]:
    """Maps each physical cell's (row, col) anchor -> (rowspan, colspan),
    derived from the real cell rectangles rather than guessed from which
    grid slots extract() left as None -- a None slot is ambiguous (could be
    a genuinely blank single cell, or covered by a span from another
    direction) but the cell geometry itself is not."""
    x_bounds = sorted({round(c[0], 1) for c in cells} | {round(c[2], 1) for c in cells})
    y_bounds = sorted({round(c[1], 1) for c in cells} | {round(c[3], 1) for c in cells})
    col_index = {v: i for i, v in enumerate(x_bounds)}
    row_index = {v: i for i, v in enumerate(y_bounds)}

    lookup: dict[tuple[int, int], tuple[int, int]] = {}
    for x0, y0, x1, y1 in cells:
        r0, r1 = row_index[round(y0, 1)], row_index[round(y1, 1)]
        c0, c1 = col_index[round(x0, 1)], col_index[round(x1, 1)]
        lookup[(r0, c0)] = (r1 - r0, c1 - c0)
    return lookup


def _extract_table(table: Any, page_number: int) -> dict[str, Any]:
    cells = table.cells
    bbox = [round(v, 2) for v in table.bbox]
    if not cells:
        return {
            "page_range": [page_number, page_number],
            "bbox": bbox,
            "caption": None,
            "headers": [],
            "rows": [],
        }

    span_lookup = _span_lookup(cells)
    grid: list[list[str | None]] = table.extract()

    # A row consisting of exactly one value at column 0, matching "Table
    # N..."/"Figure N...", is the table's own embedded caption line --
    # confirmed real for this corpus's reference table, where the caption
    # is literally the first row of the detected grid, not separate page
    # text above it.
    data_start = 0
    caption: str | None = None
    if (
        grid
        and _row_text_count(grid[0]) == 1
        and grid[0][0]
        and CAPTION_PATTERN.match(grid[0][0].strip())
    ):
        caption = grid[0][0].strip()
        data_start = 1

    headers: list[dict[str, Any] | None] = []
    rows_start = data_start
    if data_start < len(grid):
        headers = [_parse_header(v) if v is not None else None for v in grid[data_start]]
        rows_start = data_start + 1

    rows = []
    for r in range(rows_start, len(grid)):
        row_cells = []
        for c, value in enumerate(grid[r]):
            if value is None:
                continue  # covered by another cell's rowspan/colspan, not a real cell here
            rowspan, colspan = span_lookup.get((r, c), (1, 1))
            row_cells.append({"col": c, "text": value, "rowspan": rowspan, "colspan": colspan})
        rows.append(row_cells)

    return {
        "page_range": [page_number, page_number],
        "bbox": bbox,
        "caption": caption,
        "headers": headers,
        "rows": rows,
    }


def _find_preceding_caption(
    page_blocks: list[dict[str, Any]], table_bbox: tuple[float, float, float, float]
) -> str | None:
    """Fallback for a table whose caption sits in ordinary page text above
    it rather than being folded into the table's own first row -- the more
    general case the plan describes; the reference table happens to embed
    its caption in-grid instead (see _extract_table)."""
    table_top = table_bbox[1]
    candidates = [
        b
        for b in page_blocks
        if b["bbox"][3] <= table_top and CAPTION_PATTERN.match(b["text"].strip())
    ]
    if not candidates:
        return None
    closest = max(candidates, key=lambda b: b["bbox"][3])
    return str(closest["text"]).strip()


def extract_page_tables(
    page: fitz.Page, page_blocks: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    finder = page.find_tables()
    tables = []
    for table in finder.tables:
        raw = _extract_table(table, page.number)
        if raw["caption"] is None and page_blocks:
            raw["caption"] = _find_preceding_caption(page_blocks, table.bbox)
        tables.append(raw)
    return tables


def propagate_headers(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Carries a table's headers forward onto a later table sharing its
    caption but missing its own headers -- the single-page mechanism a
    multi-page continuation page (S2-06 formalizes full reassembly) relies
    on when it doesn't repeat its header row."""
    last_headers_by_caption: dict[str, list[dict[str, Any] | None]] = {}
    result = []
    for table in tables:
        caption = table.get("caption")
        headers = table.get("headers") or []
        has_headers = any(h is not None for h in headers)
        if caption and has_headers:
            last_headers_by_caption[caption] = headers
        elif caption and not has_headers and caption in last_headers_by_caption:
            table = {
                **table,
                "headers": last_headers_by_caption[caption],
                "headers_propagated": True,
            }
        result.append(table)
    return result


def _overlapping_section_label(page_range: list[int], sections: list[dict[str, Any]] | None) -> str:
    if not sections:
        return UNCLASSIFIED
    start, end = page_range
    for section in sections:
        s0, s1 = section["page_range"]
        if s0 <= end and start <= s1:
            return str(section["label"])
    return UNCLASSIFIED


def extract_document_tables(
    pdf_path: Path,
    document_content: dict[str, Any],
    sections: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    blocks_by_page = {p["page_number"]: p["blocks"] for p in document_content["pages"]}

    doc = fitz.open(pdf_path)
    try:
        tables: list[dict[str, Any]] = []
        errors: list[tuple[int, str]] = []
        for i in range(doc.page_count):
            try:
                tables.extend(extract_page_tables(doc[i], blocks_by_page.get(i)))
            except PAGE_ERROR_TYPES as exc:
                # find_tables() can find a visual grid it can't fully parse
                # -- flag the page and keep going rather than lose every
                # other table in a multi-hundred-page document over one bad
                # page.
                errors.append((i, str(exc)))
    finally:
        doc.close()

    tables = propagate_headers(tables)
    for table in tables:
        table["source_section"] = _overlapping_section_label(table["page_range"], sections)
    return tables, errors


def extracted_documents(extracted_dir: Path = DEFAULT_EXTRACTED_DIR) -> list[Path]:
    return sorted(extracted_dir.glob("*/*.json"))


def tables_corpus(
    extracted_dir: Path = DEFAULT_EXTRACTED_DIR,
    sections_dir: Path = DEFAULT_SECTIONS_DIR,
    dest_dir: Path = DEFAULT_DEST_DIR,
) -> dict[str, Any]:
    documents = 0
    total_tables = 0
    page_errors: list[str] = []

    for path in extracted_documents(extracted_dir):
        document_content = json.loads(path.read_text())
        pdf_path = Path(document_content["source_path"])

        sections_path = sections_dir / document_content["nct_id"] / path.name
        sections = (
            json.loads(sections_path.read_text())["sections"] if sections_path.exists() else None
        )

        tables, errors = extract_document_tables(pdf_path, document_content, sections=sections)
        for page_number, message in errors:
            page_errors.append(
                f"{document_content['nct_id']}\t{document_content['doc_type']}\t{page_number}\t{message}"
            )

        dest_path = dest_dir / document_content["nct_id"] / path.name
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(
            json.dumps(
                {
                    "nct_id": document_content["nct_id"],
                    "doc_type": document_content["doc_type"],
                    "tables": tables,
                },
                indent=2,
            )
            + "\n"
        )
        documents += 1
        total_tables += len(tables)

    if page_errors:
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "extraction_failures.log").write_text("\n".join(page_errors) + "\n")

    summary = {"documents": documents, "tables": total_tables, "failed_pages": len(page_errors)}
    print(
        f"Extracted tables from {documents} document(s): {total_tables} table(s), "
        f"{len(page_errors)} page failure(s)"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract tables from the segmented corpus.")
    parser.add_argument("--extracted-dir", type=Path, default=DEFAULT_EXTRACTED_DIR)
    parser.add_argument("--sections-dir", type=Path, default=DEFAULT_SECTIONS_DIR)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_DIR)
    args = parser.parse_args()

    tables_corpus(
        extracted_dir=args.extracted_dir, sections_dir=args.sections_dir, dest_dir=args.dest
    )


if __name__ == "__main__":
    main()
