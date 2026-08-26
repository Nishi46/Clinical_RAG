"""Assessment-schedule handling -- multi-page table reassembly.

Post-processes S2-05's per-page table output (data/tables/{nct_id}/{doc_type}.json),
collapsing a run of continuation pages into one logical table. Ground-truthed
against the corpus's confirmed real multi-page assessment tables
(corpus_assessment.md Sec.6): NCT02872116 protocol's Table 5.1-2 spans
0-indexed pages 83-85 (22 data rows: 9+10+3) and the adjacent Table 5.1-3
spans pages 86-90 (23 data rows: 8+4+7+3+1) -- both confirmed by directly
counting rows per page via S2-05's own extractor, not assumed from prose.
Table 5.1-2 and 5.1-3 sit on strictly adjacent pages (85 -> 86) but are
correctly kept separate because their column headers differ (each arm's
visit-schedule columns are named for that arm's regimen) -- header equality
is what actually discriminates a real continuation from an adjacent-but-
unrelated table, not adjacency alone.

Reads and rewrites data/tables/ in place; never re-runs S2-05's PDF table
extraction (which is the expensive step).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_TABLES_DIR = Path("data/tables")

CONTINUED_MARKER = re.compile(r"\(\s*cont(?:'d|inued)?\.?\s*\)", re.IGNORECASE)


def _header_texts(table: dict[str, Any]) -> list[str | None]:
    return [h["text"] if h else None for h in table.get("headers") or []]


def is_continuation_table(table_a: dict[str, Any], table_b: dict[str, Any]) -> bool:
    """table_b continues table_a: same column-header set (post S2-05
    propagation), and either they sit on strictly adjacent pages or
    table_b's caption carries an explicit "(continued)" marker. Header
    equality is required unconditionally -- two unrelated tables can easily
    be adjacent (confirmed real: Table 5.1-2's last page is immediately
    followed by Table 5.1-3's first page), so adjacency alone is not
    sufficient evidence of continuation.
    """
    headers_a = _header_texts(table_a)
    headers_b = _header_texts(table_b)
    if not headers_a or headers_a != headers_b:
        return False

    adjacent = table_b["page_range"][0] == table_a["page_range"][1] + 1
    caption_b = table_b.get("caption")
    continued_marker = bool(caption_b and CONTINUED_MARKER.search(caption_b))
    return adjacent or continued_marker


def merge_tables(tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Reassembles a confirmed run of continuation tables into one logical
    table: rows concatenated in page order, headers/caption taken once from
    the first table in the run. bbox is page-local and not meaningful
    merged across pages -- set to None; per-page bboxes remain in
    _raw_pages for anyone who needs them."""
    first = tables[0]
    merged: dict[str, Any] = {
        "page_range": [tables[0]["page_range"][0], tables[-1]["page_range"][1]],
        "bbox": None,
        "caption": first.get("caption"),
        "headers": first.get("headers", []),
        "rows": [row for t in tables for row in t["rows"]],
        "source_section": first.get("source_section"),
        "merged_from_pages": [t["page_range"][0] for t in tables],
    }
    if first.get("headers_propagated"):
        merged["headers_propagated"] = True
    return merged


def reassemble_document_tables(raw_tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs merge_tables over every table in a document, in page order,
    collapsing multi-page continuation runs while leaving genuinely
    independent single-page tables untouched."""
    if not raw_tables:
        return []

    result: list[dict[str, Any]] = []
    run = [raw_tables[0]]
    for table in raw_tables[1:]:
        if is_continuation_table(run[-1], table):
            run.append(table)
        else:
            result.append(merge_tables(run) if len(run) > 1 else run[0])
            run = [table]
    result.append(merge_tables(run) if len(run) > 1 else run[0])
    return result


def reassemble_corpus(tables_dir: Path = DEFAULT_TABLES_DIR) -> dict[str, Any]:
    documents = 0
    total_raw = 0
    total_merged = 0

    for path in sorted(tables_dir.glob("*/*.json")):
        payload = json.loads(path.read_text())
        # Re-running is safe: once a file carries _raw_pages (from a prior
        # run), that -- not the already-merged "tables" -- is the true
        # per-page source. Re-merging already-merged tables would risk a
        # wrong second merge (their page_range already spans multiple
        # pages, which can coincidentally look "adjacent" to a neighbor).
        raw_tables = payload.get("_raw_pages", payload["tables"])
        merged = reassemble_document_tables(raw_tables)

        payload["_raw_pages"] = raw_tables
        payload["tables"] = merged
        path.write_text(json.dumps(payload, indent=2) + "\n")

        documents += 1
        total_raw += len(raw_tables)
        total_merged += len(merged)

    summary = {"documents": documents, "raw_tables": total_raw, "merged_tables": total_merged}
    print(
        f"Reassembled {documents} document(s): {total_raw} raw table(s) -> "
        f"{total_merged} logical table(s)"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reassemble multi-page tables in the S2-05 table-extraction output."
    )
    parser.add_argument("--tables-dir", type=Path, default=DEFAULT_TABLES_DIR)
    args = parser.parse_args()

    reassemble_corpus(tables_dir=args.tables_dir)


if __name__ == "__main__":
    main()
