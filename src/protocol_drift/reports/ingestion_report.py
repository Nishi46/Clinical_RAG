"""Ingestion quality report — docs/ingestion.md, regenerated from frozen
Sprint 2 artifacts.

Reads only data/sections/, data/tables/, data/chunks/, data/chunks_naive/,
and data/ocr_backlog.json (plus Postgres `trials` for the sponsor-class
join S1-05 already loads there) -- never re-extracts, re-segments, or
re-chunks anything. Matches S1-09's scripts/corpus_report.py pattern: every
number traces back to a file on disk, never hand-typed into markdown.

The naive-vs-section-aware comparison is built on NCT02872116 specifically
(corpus_assessment.md Sec.6's confirmed multi-page assessment-schedule
table) -- it is not part of the randomly sampled 200-trial cohort, so its
data/chunks_naive/ and data/chunks/ output was backfilled once via the
same chunk_naive.py/chunk.py functions the real corpus uses, precisely so
this report can read it as a frozen artifact like everything else.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from protocol_drift.ingestion.sections import UNCLASSIFIED, lookup_sponsors

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_SECTIONS_DIR = Path("data/sections")
DEFAULT_TABLES_DIR = Path("data/tables")
DEFAULT_CHUNKS_DIR = Path("data/chunks")
DEFAULT_CHUNKS_NAIVE_DIR = Path("data/chunks_naive")
DEFAULT_OCR_BACKLOG_PATH = Path("data/ocr_backlog.json")
DEFAULT_OUT_PATH = Path("docs/ingestion.md")

# The literal deliverable S2-10 (and the eventual blog post) needs: a real
# multi-page table torn apart by the naive chunker, next to the same table
# as one clean chunk_type="table" (here: "assessment_schedule") chunk.
COMPARISON_NCT_ID = "NCT02872116"
COMPARISON_DOC_TYPE = "protocol"
COMPARISON_PAGE_RANGE = (86, 90)  # Table 5.1-3, corpus_assessment.md Sec.6


# --- loading frozen artifacts, scoped to the real cohort ---------------------


def _cohort_nct_ids(cohort_path: Path) -> set[str]:
    cohort = json.loads(cohort_path.read_text())
    return {t["nct_id"] for t in cohort["trials"]}


def load_section_docs(sections_dir: Path, cohort_nct_ids: set[str]) -> list[dict[str, Any]]:
    """Every S2-04 output belonging to the frozen cohort. NCT02872116 (the
    naive-vs-aware comparison fixture) is deliberately excluded here so it
    doesn't skew the corpus-wide detection-rate numbers -- it was never
    part of the sampled cohort those numbers describe."""
    docs = []
    for path in sorted(sections_dir.glob("*/*.json")):
        if path.parent.name not in cohort_nct_ids:
            continue
        docs.append(json.loads(path.read_text()))
    return docs


def load_table_docs(tables_dir: Path, cohort_nct_ids: set[str]) -> list[dict[str, Any]]:
    docs = []
    for path in sorted(tables_dir.glob("*/*.json")):
        if path.parent.name not in cohort_nct_ids:
            continue
        docs.append(json.loads(path.read_text()))
    return docs


def load_chunk_files(chunks_dir: Path, cohort_nct_ids: set[str]) -> list[Path]:
    return [p for p in sorted(chunks_dir.glob("*/*.jsonl")) if p.parent.name in cohort_nct_ids]


# --- section detection ---------------------------------------------------------


def _is_detected(section_doc: dict[str, Any]) -> bool:
    return any(s["label"] != UNCLASSIFIED for s in section_doc["sections"])


def section_detection_rate(section_docs: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(section_docs)
    detected = sum(1 for d in section_docs if _is_detected(d))
    return {
        "documents": total,
        "detected": detected,
        "rate": round(100 * detected / total, 1) if total else 0.0,
    }


def section_detection_rate_by_sponsor_class(
    section_docs: list[dict[str, Any]], sponsor_lookup: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    by_class: dict[str, list[bool]] = defaultdict(list)
    for d in section_docs:
        sponsor_class = sponsor_lookup.get(d["nct_id"], {}).get("sponsor_class") or "UNKNOWN"
        by_class[sponsor_class].append(_is_detected(d))

    return {
        cls: {
            "documents": len(flags),
            "detected": sum(flags),
            "rate": round(100 * sum(flags) / len(flags), 1) if flags else 0.0,
        }
        for cls, flags in sorted(by_class.items())
    }


def document_depth_split(section_docs: list[dict[str, Any]]) -> dict[str, Any]:
    """corpus_assessment.md Sec.4's finding as a number: some "protocols" are
    thin summaries with zero real sections, others get full coverage with
    no unclassified gap at all, and most fall somewhere in between."""
    zero = full = partial = 0
    for d in section_docs:
        labels = [s["label"] for s in d["sections"]]
        has_named = any(label != UNCLASSIFIED for label in labels)
        has_gap = any(label == UNCLASSIFIED for label in labels)
        if not has_named:
            zero += 1
        elif not has_gap:
            full += 1
        else:
            partial += 1

    total = len(section_docs)

    def _pct(n: int) -> float:
        return round(100 * n / total, 1) if total else 0.0

    return {
        "documents": total,
        "zero_sections": zero,
        "zero_sections_pct": _pct(zero),
        "full_coverage": full,
        "full_coverage_pct": _pct(full),
        "partial": partial,
        "partial_pct": _pct(partial),
    }


def count_detection_failures(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    return len([line for line in log_path.read_text().splitlines() if line.strip()])


# --- tables ----------------------------------------------------------------


def table_reassembly_stats(table_docs: list[dict[str, Any]]) -> dict[str, Any]:
    documents_with_tables = 0
    total_raw = 0
    total_logical = 0
    for d in table_docs:
        raw = d.get("_raw_pages", d["tables"])
        if raw:
            documents_with_tables += 1
        total_raw += len(raw)
        total_logical += len(d["tables"])
    return {
        "documents": len(table_docs),
        "documents_with_tables": documents_with_tables,
        "raw_tables": total_raw,
        "logical_tables": total_logical,
        "collapsed_by_reassembly": total_raw - total_logical,
    }


# --- chunks ----------------------------------------------------------------


def chunk_stats(chunk_files: list[Path]) -> dict[str, Any]:
    per_doc_counts: list[int] = []
    type_counts: Counter[str] = Counter()
    is_ocr_count = 0
    total_chunks = 0

    for path in chunk_files:
        lines = path.read_text().splitlines()
        per_doc_counts.append(len(lines))
        for line in lines:
            chunk = json.loads(line)
            type_counts[chunk["chunk_type"]] += 1
            is_ocr_count += bool(chunk["is_ocr"])
            total_chunks += 1

    return {
        "documents": len(chunk_files),
        "total_chunks": total_chunks,
        "mean_per_doc": round(statistics.mean(per_doc_counts), 1) if per_doc_counts else 0.0,
        "median_per_doc": statistics.median(per_doc_counts) if per_doc_counts else 0,
        "type_counts": dict(sorted(type_counts.items())),
        "is_ocr_chunks": is_ocr_count,
    }


# --- OCR backlog -------------------------------------------------------------


def ocr_backlog_stats(backlog: dict[str, Any]) -> dict[str, Any]:
    pages = backlog.get("pages", [])
    documents = {(p["nct_id"], p["doc_type"]) for p in pages}
    return {"pages": len(pages), "documents": len(documents)}


# --- naive vs. section-aware comparison --------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def naive_vs_aware_pair(
    chunks_naive_dir: Path,
    chunks_dir: Path,
    nct_id: str = COMPARISON_NCT_ID,
    doc_type: str = COMPARISON_DOC_TYPE,
    page_range: tuple[int, int] = COMPARISON_PAGE_RANGE,
) -> dict[str, Any]:
    """The naive chunker's mid-table cut across this table's page range,
    next to the section-aware chunker's single clean chunk covering it --
    both read straight from their respective frozen chunk files."""
    naive_chunks = _read_jsonl(chunks_naive_dir / nct_id / f"{doc_type}.jsonl")
    aware_chunks = _read_jsonl(chunks_dir / nct_id / f"{doc_type}.jsonl")

    start, end = page_range
    naive_overlap = [
        c for c in naive_chunks if c["page_range"][0] <= end and start <= c["page_range"][1]
    ]
    aware_match = [c for c in aware_chunks if tuple(c["page_range"]) == page_range]

    return {
        "nct_id": nct_id,
        "doc_type": doc_type,
        "page_range": page_range,
        "naive_chunks": naive_overlap,
        "aware_chunks": aware_match,
    }


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _snippet(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "…"


def render_comparison(pair: dict[str, Any]) -> list[str]:
    lines = [
        f"## Naive vs. section-aware: {pair['nct_id']}'s assessment-schedule table",
        "",
        f"`Table 5.1-3` (`corpus_assessment.md` Sec.6) spans 0-indexed pages "
        f"{pair['page_range'][0]}-{pair['page_range'][1]}. S2-02's naive 512-token chunker has no "
        "table awareness and cuts straight through it; S2-08's section-aware chunker keeps the "
        "whole table as one chunk.",
        "",
    ]

    naive_chunks = pair["naive_chunks"]
    if not naive_chunks:
        lines += ["_No naive chunks found for this document/page range._", ""]
    else:
        lines += [
            f"### Naive chunker (`data/chunks_naive/`) — table torn across "
            f"{len(naive_chunks)} chunk(s)",
            "",
            _md_table(
                ["Chunk", "Pages", "Tokens"],
                [
                    [
                        str(c["chunk_index"]),
                        f"{c['page_range'][0]}-{c['page_range'][1]}",
                        str(len(c["text"].split())),
                    ]
                    for c in naive_chunks
                ],
            ),
            "",
        ]
        if len(naive_chunks) >= 2:
            first, second = naive_chunks[0], naive_chunks[1]
            lines += [
                f"The seam between chunks {first['chunk_index']} and {second['chunk_index']} "
                "lands mid-sentence inside a single table cell -- the mangled cut that's the "
                "whole point of this comparison:",
                "",
                "```",
                "..." + first["text"][-300:],
                "---- chunk boundary ----",
                second["text"][:300] + "...",
                "```",
                "",
            ]

    aware_chunks = pair["aware_chunks"]
    if not aware_chunks:
        lines += ["_No section-aware chunk found for this document/page range._", ""]
    else:
        lines += [
            f"### Section-aware chunker (`data/chunks/`) — {len(aware_chunks)} clean chunk(s)",
            "",
        ]
        for chunk in aware_chunks:
            lines += [
                f"**Chunk {chunk['chunk_index']}**, `chunk_type={chunk['chunk_type']}`, pages "
                f"{chunk['page_range'][0]}-{chunk['page_range'][1]}:",
                "",
                "```",
                _snippet(chunk["text"], 600),
                "```",
                "",
            ]

    return lines


# --- render + generate --------------------------------------------------------


def render_report(
    section_docs: list[dict[str, Any]],
    sponsor_lookup: dict[str, dict[str, Any]],
    detection_failures_count: int,
    table_docs: list[dict[str, Any]],
    chunk_files: list[Path],
    ocr_backlog: dict[str, Any],
    comparison: dict[str, Any],
) -> str:
    detection = section_detection_rate(section_docs)
    by_sponsor = section_detection_rate_by_sponsor_class(section_docs, sponsor_lookup)
    depth = document_depth_split(section_docs)
    tables = table_reassembly_stats(table_docs)
    chunks = chunk_stats(chunk_files)
    ocr = ocr_backlog_stats(ocr_backlog)

    lines = [
        "# Ingestion quality report — Sprint 2",
        "",
        "Regenerated from `data/sections/`, `data/tables/`, `data/chunks/`, "
        "`data/chunks_naive/`, and `data/ocr_backlog.json` — no number below is hand-typed. "
        "Regenerate with `make ingestion-report`.",
        "",
        "## Section detection",
        "",
        _md_table(
            ["Metric", "Value"],
            [
                ["Documents", str(detection["documents"])],
                [
                    "≥1 canonical section detected",
                    f"{detection['detected']} ({detection['rate']}%)",
                ],
                ["Fully unclassified (0 canonical sections)", str(detection_failures_count)],
            ],
        ),
        "",
        "Every fully-unclassified document is logged with its sponsor name and sponsor class "
        "in `data/sections/detection_failures.log`.",
        "",
        f"Sprint 2 acceptance criteria requires ≥80% section-detection rate — measured "
        f"**{detection['rate']}%**.",
        "",
        "### By sponsor class",
        "",
        _md_table(
            ["Sponsor class", "Documents", "Detected", "Rate"],
            [
                [cls, str(v["documents"]), str(v["detected"]), f"{v['rate']}%"]
                for cls, v in by_sponsor.items()
            ],
        ),
        "",
        "## Document-depth split",
        "",
        "The corpus mixes full protocols/SAPs against thin 2-3 page academic summaries with no "
        "assessment table at all (`corpus_assessment.md` Sec.4) — visible here as a number, not "
        "just a note.",
        "",
        _md_table(
            ["Bucket", "Documents", "Share"],
            [
                [
                    "Zero sections detected",
                    str(depth["zero_sections"]),
                    f"{depth['zero_sections_pct']}%",
                ],
                [
                    "Full section coverage (no unclassified gap)",
                    str(depth["full_coverage"]),
                    f"{depth['full_coverage_pct']}%",
                ],
                ["Partial", str(depth["partial"]), f"{depth['partial_pct']}%"],
            ],
        ),
        "",
        "## Tables",
        "",
        _md_table(
            ["Metric", "Value"],
            [
                ["Documents", str(tables["documents"])],
                ["Documents with ≥1 table", str(tables["documents_with_tables"])],
                ["Raw per-page tables", str(tables["raw_tables"])],
                ["Reassembled logical tables", str(tables["logical_tables"])],
                ["Multi-page runs collapsed by S2-06", str(tables["collapsed_by_reassembly"])],
            ],
        ),
        "",
        "## Chunks",
        "",
        _md_table(
            ["Metric", "Value"],
            [
                ["Documents chunked", str(chunks["documents"])],
                ["Total chunks", str(chunks["total_chunks"])],
                ["Chunks per document (mean)", str(chunks["mean_per_doc"])],
                ["Chunks per document (median)", str(chunks["median_per_doc"])],
                ["`is_ocr` chunks", str(chunks["is_ocr_chunks"])],
            ],
        ),
        "",
        "### Chunk-type breakdown",
        "",
        _md_table(["Type", "Count"], [[t, str(n)] for t, n in chunks["type_counts"].items()]),
        "",
        "## OCR backlog",
        "",
        f"`data/ocr_backlog.json` enumerates **{ocr['pages']}** page(s) across "
        f"**{ocr['documents']}** document(s) needing OCR; the default pipeline skips and "
        "reports them rather than attempting extraction (S2-03).",
        "",
    ]

    lines += render_comparison(comparison)

    return "\n".join(lines) + "\n"


def generate_ingestion_report(
    cohort_path: Path = DEFAULT_COHORT_PATH,
    sections_dir: Path = DEFAULT_SECTIONS_DIR,
    tables_dir: Path = DEFAULT_TABLES_DIR,
    chunks_dir: Path = DEFAULT_CHUNKS_DIR,
    chunks_naive_dir: Path = DEFAULT_CHUNKS_NAIVE_DIR,
    ocr_backlog_path: Path = DEFAULT_OCR_BACKLOG_PATH,
    out_path: Path = DEFAULT_OUT_PATH,
    sponsor_lookup_fn: Callable[[list[str]], dict[str, dict[str, Any]]] = lookup_sponsors,
) -> str:
    cohort_nct_ids = _cohort_nct_ids(cohort_path)
    section_docs = load_section_docs(sections_dir, cohort_nct_ids)
    table_docs = load_table_docs(tables_dir, cohort_nct_ids)
    chunk_files = load_chunk_files(chunks_dir, cohort_nct_ids)
    ocr_backlog = json.loads(ocr_backlog_path.read_text()) if ocr_backlog_path.exists() else {}
    detection_failures_count = count_detection_failures(sections_dir / "detection_failures.log")
    sponsor_lookup = sponsor_lookup_fn(sorted({d["nct_id"] for d in section_docs}))
    comparison = naive_vs_aware_pair(chunks_naive_dir, chunks_dir)

    report = render_report(
        section_docs,
        sponsor_lookup,
        detection_failures_count,
        table_docs,
        chunk_files,
        ocr_backlog,
        comparison,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate docs/ingestion.md from frozen artifacts."
    )
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--sections-dir", type=Path, default=DEFAULT_SECTIONS_DIR)
    parser.add_argument("--tables-dir", type=Path, default=DEFAULT_TABLES_DIR)
    parser.add_argument("--chunks-dir", type=Path, default=DEFAULT_CHUNKS_DIR)
    parser.add_argument("--chunks-naive-dir", type=Path, default=DEFAULT_CHUNKS_NAIVE_DIR)
    parser.add_argument("--ocr-backlog", type=Path, default=DEFAULT_OCR_BACKLOG_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument(
        "--dsn", default=None, help="Postgres DSN override for the sponsor-class join"
    )
    args = parser.parse_args()

    sponsor_lookup_fn = (
        (lambda ids: lookup_sponsors(ids, dsn=args.dsn)) if args.dsn else lookup_sponsors
    )

    generate_ingestion_report(
        cohort_path=args.cohort,
        sections_dir=args.sections_dir,
        tables_dir=args.tables_dir,
        chunks_dir=args.chunks_dir,
        chunks_naive_dir=args.chunks_naive_dir,
        ocr_backlog_path=args.ocr_backlog,
        out_path=args.out,
        sponsor_lookup_fn=sponsor_lookup_fn,
    )
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
