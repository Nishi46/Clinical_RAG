"""Section-aware chunker -- the "after" picture S2-10 compares against
S2-02's naive baseline.

Unlike chunk_naive.py's fixed-size token windows, this walks the document
respecting S2-04's section boundaries as hard chunk breaks (a chunk never
spans two canonical sections) and S2-06's reassembled logical tables as
atomic units (a chunk never splits a table mid-row). Every chunk is
prefixed with a contextual header string built from S2-07's reconciled
per-page version, so a retrieved chunk is self-describing outside its
source document.

Reads only from data/extracted/ (S2-01), data/sections/ (S2-04),
data/tables/ (S2-05/S2-06, post-reassembly), and data/versions/ (S2-07),
never re-derives any of them.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from protocol_drift.ingestion.models import Chunk
from protocol_drift.ingestion.sections import UNCLASSIFIED

DEFAULT_EXTRACTED_DIR = Path("data/extracted")
DEFAULT_SECTIONS_DIR = Path("data/sections")
DEFAULT_TABLES_DIR = Path("data/tables")
DEFAULT_VERSIONS_DIR = Path("data/versions")
DEFAULT_DEST_DIR = Path("data/chunks")

# Reuses S2-02's ~512-token body-text target by default. The table ceiling
# is raised, not shared: the real reference table (NCT02872116's Table
# 5.1-3, corpus_assessment.md Sec.6) renders to ~709 whitespace tokens --
# already past 512 -- and splitting a table that narrowly overflows the
# plain text budget would defeat the entire point of this chunker. 1024
# comfortably covers every confirmed real table in the corpus while still
# bounding a pathologically large one.
DEFAULT_CHUNK_TOKENS = 512
DEFAULT_TABLE_CHUNK_TOKENS = 1024


def _token_count(text: str) -> int:
    """Same simple whitespace token count as S2-02's naive chunker -- not a
    real BPE tokenizer, and deliberately consistent with it so the two
    chunkers' token budgets mean the same thing in S2-10's comparison."""
    return len(text.split())


def _overlaps(a: list[int], b: list[int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


# --- body text: block-boundary chunking within one section -----------------


def _blocks_in_section(
    document_content: dict[str, Any], section: dict[str, Any], table_pages: set[int]
) -> list[tuple[int, dict[str, Any]]]:
    """This section's blocks, in page order, excluding any page a table
    overlaps. S2-01 extracts a table's cell text as ordinary ungrouped
    blocks too (get_text() and find_tables() are independent passes over
    the same page) -- without this exclusion, a table's own content would
    also show up scrambled in the surrounding text chunk. Dropping the
    *whole* page rather than just the table's bbox region is a deliberate
    simplification: these tables run close to full-page, so the loss is
    small, and precise bbox-overlap filtering isn't worth the complexity
    it would add within this task's scope."""
    start, end = section["page_range"]
    blocks = []
    for page in document_content["pages"]:
        page_number = page["page_number"]
        if not (start <= page_number <= end) or page_number in table_pages:
            continue
        for block in page["blocks"]:
            blocks.append((page_number, block))
    return blocks


def _chunk_blocks(
    blocks: list[tuple[int, dict[str, Any]]], chunk_tokens: int
) -> list[dict[str, Any]]:
    """Greedily packs whole blocks into token-budget windows -- a chunk
    boundary always falls between two blocks, never inside one, so this
    never cuts mid-sentence except in the unavoidable case where a single
    block already exceeds the budget on its own (left intact as one
    over-budget chunk rather than force-split)."""
    groups: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_tokens = 0
    for page_number, block in blocks:
        text = block["text"]
        tokens = _token_count(text)
        if current and current_tokens + tokens > chunk_tokens:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append((page_number, text))
        current_tokens += tokens
    if current:
        groups.append(current)

    return [
        {
            "page_range": [min(p for p, _ in g), max(p for p, _ in g)],
            "text": " ".join(t for _, t in g),
        }
        for g in groups
    ]


# --- tables: whole-table or row-split chunking ------------------------------


def _header_cell_text(header: dict[str, Any] | None) -> str:
    if header is None:
        return ""
    text = str(header.get("text", ""))
    unit = header.get("unit")
    return f"{text} ({unit})" if unit else text


def _row_line(row: list[dict[str, Any]], n_cols: int) -> str:
    if n_cols == 0:
        return " | ".join(str(cell["text"]) for cell in row)
    cells = [""] * n_cols
    for cell in row:
        col = cell["col"]
        if col < n_cols:
            cells[col] = str(cell["text"])
    return " | ".join(cells)


def _render_table_text(table: dict[str, Any], rows: list[Any] | None = None) -> str:
    """Plain-text rendering of a table (or one row-group of it) for
    embedding: caption, then the propagated header line, then one line per
    row -- units folded into the header cell they belong to (S2-05) so they
    aren't lost in this flattening."""
    headers = table.get("headers") or []
    n_cols = len(headers)
    lines = []
    caption = table.get("caption")
    if caption:
        lines.append(str(caption))
    if headers:
        lines.append(" | ".join(_header_cell_text(h) for h in headers))
    for row in rows if rows is not None else table["rows"]:
        lines.append(_row_line(row, n_cols))
    return "\n".join(lines)


def _table_header_tokens(table: dict[str, Any]) -> int:
    headers = table.get("headers") or []
    caption = table.get("caption")
    lines = []
    if caption:
        lines.append(str(caption))
    if headers:
        lines.append(" | ".join(_header_cell_text(h) for h in headers))
    return _token_count("\n".join(lines))


def _split_table_rows(table: dict[str, Any], table_chunk_tokens: int) -> list[list[Any]]:
    """Row-group boundaries for a table too large even for the raised
    ceiling: a row is always kept whole (never split across groups), and
    every group's token budget accounts for the header+caption that will
    be repeated at the top of it, so no group can silently overflow once
    rendered."""
    n_cols = len(table.get("headers") or [])
    base_tokens = _table_header_tokens(table)

    groups: list[list[Any]] = []
    current: list[Any] = []
    current_tokens = base_tokens
    for row in table["rows"]:
        row_tokens = _token_count(_row_line(row, n_cols))
        if current and current_tokens + row_tokens > table_chunk_tokens:
            groups.append(current)
            current = []
            current_tokens = base_tokens
        current.append(row)
        current_tokens += row_tokens
    if current:
        groups.append(current)
    return groups


ChunkType = Literal["text", "table", "assessment_schedule"]


def _table_chunk_type(table: dict[str, Any]) -> ChunkType:
    if table.get("source_section") == "assessment_schedule":
        return "assessment_schedule"
    return "table"


# --- version lookup + contextual header -------------------------------------


def _version_for_page_range(
    page_range: list[int], versions: list[dict[str, Any]]
) -> int | float | None:
    """The reconciled S2-07 version for a chunk's pages -- the first
    version-timeline record whose range overlaps it. A chunk that happens
    to straddle a version transition (rare, and not something S2-04's
    section boundaries are aware of) just takes that first match rather
    than trying to represent two versions in one header."""
    for record in versions:
        if _overlaps(page_range, record["page_range"]) and record.get("version_marker"):
            version = record["version_marker"]["version"]
            return version if isinstance(version, int | float) else None
    return None


def _contextual_header(
    nct_id: str, doc_type: str, doc_version: int | float | None, section_path: str
) -> str:
    version_label = f"v{doc_version}" if doc_version is not None else "v?"
    return f"[{nct_id} | {doc_type} {version_label} | {section_path}]"


def _is_ocr_for_page_range(document_content: dict[str, Any], page_range: list[int]) -> bool:
    """True if any page this chunk's range covers required OCR -- per
    S2-01/S2-03, a needs_ocr page contributes no blocks of its own, so it
    never shows up as a block source directly, but it can still fall
    *inside* a text chunk's page range when its non-OCR neighbors' content
    gets packed into one chunk around it. The flag records that provenance
    regardless of whether --with-ocr actually ran (S2-03's default path
    skips it), per S2-09's spec."""
    start, end = page_range
    return any(
        page["needs_ocr"]
        for page in document_content["pages"]
        if start <= page["page_number"] <= end
    )


def _build_chunk(
    document_content: dict[str, Any],
    chunk_index: int,
    chunk_type: ChunkType,
    section_label: str,
    page_range: list[int],
    body_text: str,
    versions: list[dict[str, Any]],
) -> dict[str, Any]:
    nct_id = document_content["nct_id"]
    doc_type = document_content["doc_type"]
    doc_version = _version_for_page_range(page_range, versions)
    header = _contextual_header(nct_id, doc_type, doc_version, section_label)

    # subsection is always None for now: S2-04's segmentation only ever
    # detects top-level canonical sections (or unclassified), never a
    # nested heading within one -- S2-09 spots for that field without
    # requiring the nested-heading detection that would populate it.
    chunk = Chunk(
        nct_id=nct_id,
        doc_type=doc_type,
        doc_version=doc_version,
        section=section_label,
        subsection=None,
        page_range=(page_range[0], page_range[1]),
        chunk_type=chunk_type,
        is_ocr=_is_ocr_for_page_range(document_content, page_range),
        chunk_index=chunk_index,
        text=f"{header}\n{body_text}",
    )
    return chunk.model_dump(mode="json")


def _section_for_table(
    table: dict[str, Any], sections: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The section a table belongs to, by which section's page range
    contains the table's *start* page -- not general overlap, so a table
    is never attached to more than one section even if its range brushes
    a boundary. Sections partition every page (S2-04's leading-gap +
    contiguous-marker + whole-document-fallback logic never leaves a page
    uncovered), so in practice this always finds exactly one match."""
    start_page = table["page_range"][0]
    for section in sections:
        if section["page_range"][0] <= start_page <= section["page_range"][1]:
            return section
    return None


def chunk_document(
    document_content: dict[str, Any],
    sections: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    table_chunk_tokens: int = DEFAULT_TABLE_CHUNK_TOKENS,
) -> list[dict[str, Any]]:
    """Walks the document in page order: within each section, body text is
    packed into token-budget chunks at block boundaries, followed by that
    section's own table(s) as one chunk each (row-split only if a table
    still exceeds the raised ceiling). A chunk never spans two sections --
    each section is chunked from its own isolated block list -- and a
    table is never split mid-row."""
    table_pages: set[int] = set()
    for table in tables:
        table_pages.update(range(table["page_range"][0], table["page_range"][1] + 1))

    tables_by_section_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for table in tables:
        section = _section_for_table(table, sections)
        if section is not None:
            tables_by_section_id[id(section)].append(table)

    chunks: list[dict[str, Any]] = []
    chunk_index = 0
    for section in sections:
        blocks = _blocks_in_section(document_content, section, table_pages)
        for text_chunk in _chunk_blocks(blocks, chunk_tokens):
            chunks.append(
                _build_chunk(
                    document_content,
                    chunk_index,
                    "text",
                    section["label"],
                    text_chunk["page_range"],
                    text_chunk["text"],
                    versions,
                )
            )
            chunk_index += 1

        for table in tables_by_section_id.get(id(section), []):
            chunk_type = _table_chunk_type(table)
            if _token_count(_render_table_text(table)) <= table_chunk_tokens:
                row_groups: list[list[Any] | None] = [None]
            else:
                row_groups = list(_split_table_rows(table, table_chunk_tokens))
            for rows in row_groups:
                chunks.append(
                    _build_chunk(
                        document_content,
                        chunk_index,
                        chunk_type,
                        section["label"],
                        table["page_range"],
                        _render_table_text(table, rows=rows),
                        versions,
                    )
                )
                chunk_index += 1

    return chunks


def write_chunks(chunks: list[dict[str, Any]], dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with dest_path.open("w") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk) + "\n")


def extracted_documents(extracted_dir: Path = DEFAULT_EXTRACTED_DIR) -> list[Path]:
    return sorted(extracted_dir.glob("*/*.json"))


_WHOLE_DOCUMENT_SECTION = {
    "label": UNCLASSIFIED,
    "raw_heading_text": None,
    "detection_method": "unmatched",
}


def _load_sections(
    sections_dir: Path, nct_id: str, name: str, total_pages: int
) -> list[dict[str, Any]]:
    path = sections_dir / nct_id / name
    if path.exists():
        return list(json.loads(path.read_text())["sections"])
    # No S2-04 output for this document -- degrade the same way
    # segment_document itself does when it finds no signal at all, rather
    # than erroring out on a document this chunker can still usefully chunk.
    return [{**_WHOLE_DOCUMENT_SECTION, "page_range": [0, max(total_pages - 1, 0)]}]


def _load_tables(tables_dir: Path, nct_id: str, name: str) -> list[dict[str, Any]]:
    path = tables_dir / nct_id / name
    return list(json.loads(path.read_text())["tables"]) if path.exists() else []


def _load_versions(versions_dir: Path, nct_id: str, name: str) -> list[dict[str, Any]]:
    path = versions_dir / nct_id / name
    return list(json.loads(path.read_text())["versions"]) if path.exists() else []


def chunk_corpus(
    extracted_dir: Path = DEFAULT_EXTRACTED_DIR,
    sections_dir: Path = DEFAULT_SECTIONS_DIR,
    tables_dir: Path = DEFAULT_TABLES_DIR,
    versions_dir: Path = DEFAULT_VERSIONS_DIR,
    dest_dir: Path = DEFAULT_DEST_DIR,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    table_chunk_tokens: int = DEFAULT_TABLE_CHUNK_TOKENS,
) -> dict[str, Any]:
    documents = 0
    total_chunks = 0
    for path in extracted_documents(extracted_dir):
        document_content = json.loads(path.read_text())
        nct_id = document_content["nct_id"]

        sections = _load_sections(sections_dir, nct_id, path.name, document_content["total_pages"])
        tables = _load_tables(tables_dir, nct_id, path.name)
        versions = _load_versions(versions_dir, nct_id, path.name)

        chunks = chunk_document(
            document_content,
            sections,
            tables,
            versions,
            chunk_tokens=chunk_tokens,
            table_chunk_tokens=table_chunk_tokens,
        )

        # Mirrors S2-01's own doc_type[_N].json -> [_N].jsonl naming so a
        # duplicate-doc_type trial keeps distinct chunk files.
        dest_path = dest_dir / nct_id / f"{path.stem}.jsonl"
        write_chunks(chunks, dest_path)
        documents += 1
        total_chunks += len(chunks)

    summary = {"documents": documents, "chunks": total_chunks}
    print(f"Chunked {documents} document(s) -> {total_chunks} chunks -> {dest_dir}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Section-aware chunker over the ingested corpus.")
    parser.add_argument("--extracted-dir", type=Path, default=DEFAULT_EXTRACTED_DIR)
    parser.add_argument("--sections-dir", type=Path, default=DEFAULT_SECTIONS_DIR)
    parser.add_argument("--tables-dir", type=Path, default=DEFAULT_TABLES_DIR)
    parser.add_argument("--versions-dir", type=Path, default=DEFAULT_VERSIONS_DIR)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_DIR)
    parser.add_argument("--chunk-tokens", type=int, default=DEFAULT_CHUNK_TOKENS)
    parser.add_argument("--table-chunk-tokens", type=int, default=DEFAULT_TABLE_CHUNK_TOKENS)
    args = parser.parse_args()

    chunk_corpus(
        extracted_dir=args.extracted_dir,
        sections_dir=args.sections_dir,
        tables_dir=args.tables_dir,
        versions_dir=args.versions_dir,
        dest_dir=args.dest,
        chunk_tokens=args.chunk_tokens,
        table_chunk_tokens=args.table_chunk_tokens,
    )


if __name__ == "__main__":
    main()
