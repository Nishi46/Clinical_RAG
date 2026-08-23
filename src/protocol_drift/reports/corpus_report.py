"""Corpus stats report — docs/corpus.md, regenerated from frozen artifacts.

Reads only data/cohort.json, data/pdfs/manifest.json, and
data/corpus_classification.json -- never a live query. Per the Definition of
Done ("Tables and figures regenerate via scripts/ ... never a number typed
into markdown"), every number in the report traces back to one of those
three files, and the file's own summary states the S1-G1 gate bracket
explicitly so the gate check is a read, not a re-derivation.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_PDF_MANIFEST_PATH = Path("data/pdfs/manifest.json")
DEFAULT_CLASSIFICATION_PATH = Path("data/corpus_classification.json")
DEFAULT_OUT_PATH = Path("docs/corpus.md")

# S0-03 sample findings (scratch/corpus_assessment.md) -- the point of
# comparison this report exists to update with the real frozen cohort.
S0_03_SAMPLE_PAGE_LEVEL_SCANNED_PCT = 1.1
S0_03_SAMPLE_BORN_DIGITAL_DOC_PCT = 88.0
S0_03_SAMPLE_SIZE = "20 trials / 34 documents, no therapeutic-area filter"


def gate_bracket(page_level_scanned_pct: float) -> tuple[str, str]:
    """The S1-G1 gate table from sprint_plan.md, applied to a measured rate."""
    if page_level_scanned_pct < 15:
        return "< 15%", "Proceed. OCR is a footnote."
    if page_level_scanned_pct <= 40:
        return (
            "15-40%",
            "Proceed, but budget S2-03 fully and report OCR'd content rate in every results table.",
        )
    return (
        "> 40%",
        "Re-select the cohort, biasing toward sponsors with born-digital "
        "submissions. Do not let OCR become the project.",
    )


def page_count_distribution(documents: list[dict[str, Any]]) -> dict[str, float]:
    pages = [d["total_pages"] for d in documents]
    if not pages:
        return {"min": 0, "median": 0, "mean": 0, "max": 0}
    return {
        "min": min(pages),
        "median": statistics.median(pages),
        "mean": round(statistics.mean(pages), 1),
        "max": max(pages),
    }


def page_count_histogram(
    documents: list[dict[str, Any]], bucket_size: int = 50
) -> list[tuple[str, int]]:
    buckets: dict[int, int] = {}
    for d in documents:
        b = d["total_pages"] // bucket_size
        buckets[b] = buckets.get(b, 0) + 1
    return [
        (f"{b * bucket_size}-{b * bucket_size + bucket_size - 1}", buckets[b])
        for b in sorted(buckets)
    ]


def doc_type_breakdown(pdf_entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in pdf_entries:
        counts[e["doc_type"]] = counts.get(e["doc_type"], 0) + 1
    return dict(sorted(counts.items()))


def _escape_cell(value: Any) -> str:
    # a literal "|" inside a cell (e.g. stratum keys like "INDUSTRY|PHASE1|PHASE2")
    # would otherwise be parsed as an extra column separator by any Markdown renderer.
    return str(value).replace("|", "\\|")


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    lines += ["| " + " | ".join(_escape_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def render_report(
    cohort: dict[str, Any],
    pdf_manifest: dict[str, Any],
    classification: dict[str, Any],
) -> str:
    summary = classification["summary"]
    documents = classification["documents"]
    page_level_pct = summary["scanned_page_pct_page_level"]
    bracket, action = gate_bracket(page_level_pct)

    dist = page_count_distribution(documents)
    hist = page_count_histogram(documents)
    doc_types = doc_type_breakdown(pdf_manifest["entries"])
    strat = cohort["stratification_summary"]

    lines = [
        "# Corpus stats — Sprint 1 frozen cohort",
        "",
        "Regenerated from `data/cohort.json`, `data/pdfs/manifest.json`, and "
        "`data/corpus_classification.json` — no number below is hand-typed. "
        "Regenerate with `make corpus-report`.",
        "",
        "## Overview",
        "",
        _md_table(
            ["Metric", "Value"],
            [
                ["Trials in frozen cohort", str(cohort["count"])],
                ["Documents downloaded", str(len(pdf_manifest["entries"]))],
                ["Documents classified", str(summary["documents"])],
                ["Documents failed to classify (malformed PDF)", str(summary["failed_documents"])],
                ["Total pages", str(summary["total_pages"])],
            ],
        ),
        "",
        "## Scanned-page rate",
        "",
        _md_table(
            ["Level", "Rate"],
            [
                ["Page-level (scanned pages / total pages)", f"{page_level_pct}%"],
                ["Document-level, fully born-digital", f"{summary['born_digital_doc_pct']}%"],
            ],
        ),
        "",
        "Document-level classification counts: "
        + ", ".join(f"{k}={v}" for k, v in sorted(summary["document_level_counts"].items())),
        "",
        "## vs. Sprint 0 sample",
        "",
        f"S0-03 measured this on a smaller sample ({S0_03_SAMPLE_SIZE}) before the cohort was "
        "frozen. Comparing against the real, full 200-trial cohort:",
        "",
        _md_table(
            ["Metric", "S0-03 sample", "Full cohort"],
            [
                [
                    "Page-level scanned rate",
                    f"{S0_03_SAMPLE_PAGE_LEVEL_SCANNED_PCT}%",
                    f"{page_level_pct}%",
                ],
                [
                    "Document-level born-digital rate",
                    f"{S0_03_SAMPLE_BORN_DIGITAL_DOC_PCT}%",
                    f"{summary['born_digital_doc_pct']}%",
                ],
            ],
        ),
    ]

    for flag in summary["s0_03_comparison"].values():
        if isinstance(flag, str):
            lines += ["", f"**Note:** {flag}"]

    lines += [
        "",
        "## 🚧 GATE S1-G1 — Scanned-page rate",
        "",
        f"Measured page-level scanned rate: **{page_level_pct}%** → bracket **{bracket}**.",
        "",
        f"**Action: {action}**",
        "",
        "## Page-count distribution",
        "",
        _md_table(
            ["min", "median", "mean", "max"],
            [[dist["min"], dist["median"], dist["mean"], dist["max"]]],
        ),
        "",
        "### Histogram (pages per document, 50-page buckets)",
        "",
        _md_table(["Range", "Documents"], [[r, str(n)] for r, n in hist]),
        "",
        "## Document-type distribution",
        "",
        _md_table(["Doc type", "Count"], [[k, str(v)] for k, v in doc_types.items()]),
        "",
        "## Cohort stratification (sponsor class × phase)",
        "",
        _md_table(["Stratum", "Trials"], [[k, str(v)] for k, v in sorted(strat.items())]),
        "",
    ]

    return "\n".join(lines) + "\n"


def generate_corpus_report(
    cohort_path: Path = DEFAULT_COHORT_PATH,
    pdf_manifest_path: Path = DEFAULT_PDF_MANIFEST_PATH,
    classification_path: Path = DEFAULT_CLASSIFICATION_PATH,
    out_path: Path = DEFAULT_OUT_PATH,
) -> str:
    cohort = json.loads(cohort_path.read_text())
    pdf_manifest = json.loads(pdf_manifest_path.read_text())
    classification = json.loads(classification_path.read_text())

    report = render_report(cohort, pdf_manifest, classification)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate docs/corpus.md from frozen artifacts.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--pdf-manifest", type=Path, default=DEFAULT_PDF_MANIFEST_PATH)
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args()

    generate_corpus_report(
        cohort_path=args.cohort,
        pdf_manifest_path=args.pdf_manifest,
        classification_path=args.classification,
        out_path=args.out,
    )
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
