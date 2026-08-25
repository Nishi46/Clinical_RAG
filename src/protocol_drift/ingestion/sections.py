"""Section segmentation -- canonical taxonomy + bookmark/regex detection.

Section names and positions vary by sponsor (corpus_assessment.md Sec.3:
the same "Ethical Considerations" concept appears as section 2 of 20 for
one sponsor and section 10 of 12 for another, under three different literal
headings), and PDF bookmarks exist on only ~half the corpus
(corpus_assessment.md Sec.5) -- so this can't be a fixed lookup table or a
bookmark-only approach. The taxonomy below is deliberately short, driven by
what S3 (retrieval) and S4 (discrepancy detection) actually need, not
exhaustive ICH-M11 section coverage, and its patterns are seeded from real
confirmed level-1 bookmark titles across the three sponsors profiled in
corpus_assessment.md Sec.3: Novartis (NCT02798211), BMS (NCT02872116), and
Capricor (NCT02485938). Capricor's TOC has no distinct top-level "Ethics"
entry at all -- confirmed folded into its "Administrative Considerations"
heading -- so not every canonical label appears in every document; that's
expected, not a bug.

Reads only from data/extracted/ (S2-01's output) plus the original PDFs
(for get_toc()), never re-extracts.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fitz  # pymupdf
import psycopg

from protocol_drift.db import DEFAULT_DSN

DEFAULT_EXTRACTED_DIR = Path("data/extracted")
DEFAULT_DEST_DIR = Path("data/sections")
DEFAULT_FAILURES_LOG = Path("data/sections/detection_failures.log")

UNCLASSIFIED = "unclassified"

HEADING_MIN_CHARS = 3
HEADING_MAX_CHARS = 100
HEADING_MAX_WORDS = 12
HEADING_FONT_SIZE_RATIO = 1.15  # heading candidate: font_size > body * this

# Checked in this order; first match wins. All confirmed against the real
# level-1 TOC of the three profiled sponsors' protocols -- see module
# docstring and tests/ingestion/test_sections.py.
SECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "synopsis": re.compile(r"\bsynopsis\b", re.IGNORECASE),
    "background": re.compile(r"\b(background|introduction|study rationale)\b", re.IGNORECASE),
    "objectives": re.compile(r"\b(study objectives?|objectives?)\b", re.IGNORECASE),
    "study_design": re.compile(
        r"\b(investigational plan|study design|trial design|overview of study design)\b",
        re.IGNORECASE,
    ),
    "eligibility": re.compile(
        r"\b(population|eligibility|inclusion criteria|exclusion criteria|"
        r"patient eligibility|subject eligibility)\b",
        re.IGNORECASE,
    ),
    "interventions": re.compile(
        r"\b(study drugs?|study treatments?|treatment regimen|treatment|"
        r"arms? and interventions?|investigational product)\b",
        re.IGNORECASE,
    ),
    "assessment_schedule": re.compile(
        r"\b(visit schedule|study assessments?( and procedures)?|study procedures|"
        r"study activities|schedule of (assessments|activities|events)|"
        r"assessment schedule|time and events schedule)\b",
        re.IGNORECASE,
    ),
    "statistics": re.compile(
        r"\b(statistical (considerations|methods|analysis|analyses)|data analysis|"
        r"planned statistical methods|sample size)\b",
        re.IGNORECASE,
    ),
    "ethics": re.compile(
        r"\b(ethical considerations|ethics committee|informed consent|"
        r"institutional review board|independent ethics committee)\b",
        re.IGNORECASE,
    ),
    "administrative": re.compile(
        r"\b(administrative (considerations|information)|study management|"
        r"record retention|data handling|publication policy|quality (control|assurance))\b",
        re.IGNORECASE,
    ),
}

Marker = tuple[int, "str | None", str, str]  # (start_page, label, raw_heading_text, method)


def match_canonical_label(heading_text: str) -> str | None:
    """First canonical taxonomy label whose pattern matches this heading
    text, or None if it matches none -- a real heading outside the
    taxonomy still becomes a section, just labeled unclassified rather
    than dropped."""
    for label, pattern in SECTION_PATTERNS.items():
        if pattern.search(heading_text):
            return label
    return None


def _level1_toc_markers(pdf_path: Path) -> list[Marker]:
    """Level-1 PDF bookmarks as markers -- per corpus_assessment.md Sec.5,
    roughly half the corpus has embedded bookmarks; use them as the
    primary signal when present, since heading position/wording otherwise
    varies too much across sponsors for anything simpler."""
    doc = fitz.open(pdf_path)
    try:
        toc = doc.get_toc(simple=True)  # [level, title, page (1-indexed)]
    finally:
        doc.close()

    markers = [
        (max(page - 1, 0), match_canonical_label(title), title, "bookmark")
        for level, title, page in toc
        if level == 1
    ]
    markers.sort(key=lambda m: m[0])
    return markers


def _body_font_size(document_content: dict[str, Any]) -> float:
    """Mode of rounded block font sizes across the document, as a proxy for
    "ordinary body text size" to compare heading candidates against."""
    sizes = [
        round(block["font_size"])
        for page in document_content["pages"]
        for block in page["blocks"]
        if block["font_size"] > 0
    ]
    if not sizes:
        return 0.0
    return float(Counter(sizes).most_common(1)[0][0])


def _is_heading_style(block: dict[str, Any], body_font_size: float) -> bool:
    return bool(block["bold"]) or block["font_size"] > body_font_size * HEADING_FONT_SIZE_RATIO


def _is_heading_candidate(block: dict[str, Any], body_font_size: float) -> bool:
    text = block["text"].strip()
    if not (HEADING_MIN_CHARS <= len(text) <= HEADING_MAX_CHARS):
        return False
    if len(text.split()) > HEADING_MAX_WORDS:
        return False
    return _is_heading_style(block, body_font_size)


# A numbered heading sometimes merges with the paragraph that immediately
# follows it into one PyMuPDF text block, when the PDF has no blank-line
# gap between them -- confirmed real on an NCI cooperative-group protocol
# (NCT03008278): block text "1. OBJECTIVES  1.1 Primary Objectives  1.12
# Phase 1 Primary Objective: To ..." is one bold block, ~40 words long, so
# _is_heading_candidate's length check rejects it outright even though it
# genuinely starts with a real section heading. This pulls out just the
# leading "N[.N...]. HEADING WORDS" prefix so that case is still caught.
NUMBERED_HEADING_PREFIX = re.compile(r"^\s*\d{1,2}(?:\.\d{1,2})*\.?\s+([A-Za-z][A-Za-z /\-]{2,60})")


def _numbered_heading_prefix(text: str) -> str | None:
    match = NUMBERED_HEADING_PREFIX.match(text.strip())
    return match.group(0).strip() if match else None


def _regex_scan_markers(document_content: dict[str, Any]) -> list[Marker]:
    """Fallback signal for a document with no level-1 bookmarks: flag
    short, bold/oversized blocks as heading candidates (using S2-01's
    per-block font metadata) and match them against the same taxonomy
    patterns as the bookmark path. A bold/oversized block too long to be a
    clean standalone heading still gets one more conservative check: does
    its leading numbered prefix match the taxonomy? Only added as a marker
    when it does -- this deliberately doesn't fire on every numbered
    subsection, just ones that land in a canonical bucket, to avoid turning
    dense numbered content (dose tables, revision logs) into noise.
    """
    body_font_size = _body_font_size(document_content)
    if body_font_size == 0.0:
        return []

    markers: list[Marker] = []
    for page in document_content["pages"]:
        for block in page["blocks"]:
            if _is_heading_candidate(block, body_font_size):
                text = block["text"].strip()
                markers.append((page["page_number"], match_canonical_label(text), text, "regex"))
            elif _is_heading_style(block, body_font_size):
                prefix = _numbered_heading_prefix(block["text"])
                label = match_canonical_label(prefix) if prefix else None
                if prefix is not None and label is not None:
                    markers.append((page["page_number"], label, prefix, "regex"))
    return markers


def _sections_from_markers(markers: list[Marker], total_pages: int) -> list[dict[str, Any]]:
    """Sorted (start_page, label, raw_heading_text, method) markers ->
    contiguous page-range sections. Prepends a leading unclassified section
    if the first marker doesn't start at page 0 (confirmed real: BMS's
    first level-1 bookmark, "TITLE PAGE", starts at 0-indexed page 1, not
    0 -- the true first page would otherwise go uncovered)."""
    if not markers:
        return []

    sections: list[dict[str, Any]] = []
    if markers[0][0] > 0:
        sections.append(
            {
                "label": UNCLASSIFIED,
                "raw_heading_text": None,
                "page_range": [0, markers[0][0] - 1],
                "detection_method": "unmatched",
            }
        )
    for i, (start, label, raw_heading, method) in enumerate(markers):
        end = markers[i + 1][0] - 1 if i + 1 < len(markers) else total_pages - 1
        sections.append(
            {
                "label": label or UNCLASSIFIED,
                "raw_heading_text": raw_heading,
                "page_range": [start, max(end, start)],
                "detection_method": method,
            }
        )
    return sections


def segment_document(
    document_content: dict[str, Any], pdf_path: Path | None = None
) -> list[dict[str, Any]]:
    """Section boundaries for one extracted document: level-1 bookmarks
    first, a regex heading-scan fallback when there are none, and a single
    whole-document "unclassified" section when neither signal finds
    anything -- expected on the thin 2-3 page academic summaries flagged
    in corpus_assessment.md Sec.4, rather than an error.
    """
    resolved_pdf_path = pdf_path or Path(document_content["source_path"])
    total_pages = document_content["total_pages"]

    markers = _level1_toc_markers(resolved_pdf_path)
    if not markers:
        markers = _regex_scan_markers(document_content)

    sections = _sections_from_markers(markers, total_pages)
    if not sections:
        sections = [
            {
                "label": UNCLASSIFIED,
                "raw_heading_text": None,
                "page_range": [0, max(total_pages - 1, 0)],
                "detection_method": "unmatched",
            }
        ]
    return sections


def extracted_documents(extracted_dir: Path = DEFAULT_EXTRACTED_DIR) -> list[Path]:
    """Every S2-01 extraction output -- glob excludes extraction_errors.log,
    which sits at the top level rather than under an nct_id subdirectory."""
    return sorted(extracted_dir.glob("*/*.json"))


def segment_corpus(
    extracted_dir: Path = DEFAULT_EXTRACTED_DIR,
    dest_dir: Path = DEFAULT_DEST_DIR,
) -> dict[str, Any]:
    documents = 0
    detected = 0
    fully_unclassified: list[dict[str, str]] = []
    errors: list[str] = []

    for path in extracted_documents(extracted_dir):
        document_content = json.loads(path.read_text())
        try:
            sections = segment_document(document_content)
        except (fitz.mupdf.FzErrorBase, RuntimeError) as exc:
            # Same defensive posture as S2-01/S1-07 -- a malformed PDF
            # shouldn't abort a multi-hundred-document batch.
            errors.append(f"{document_content['nct_id']}\t{document_content['doc_type']}\t{exc}")
            continue

        dest_path = dest_dir / document_content["nct_id"] / f"{path.stem}.json"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(
            json.dumps(
                {
                    "nct_id": document_content["nct_id"],
                    "doc_type": document_content["doc_type"],
                    "sections": sections,
                },
                indent=2,
            )
            + "\n"
        )

        documents += 1
        if any(s["label"] != UNCLASSIFIED for s in sections):
            detected += 1
        else:
            fully_unclassified.append(
                {"nct_id": document_content["nct_id"], "doc_type": document_content["doc_type"]}
            )

    if errors:
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "segmentation_errors.log").write_text("\n".join(errors) + "\n")

    detection_rate = detected / documents if documents else 0.0
    summary = {
        "documents": documents,
        "detected": detected,
        "detection_rate": round(detection_rate, 4),
        "fully_unclassified": len(fully_unclassified),
        "failed": len(errors),
    }
    print(
        f"Segmented {documents} document(s): {detected} with >=1 canonical section "
        f"({detection_rate:.1%} detection rate), {len(fully_unclassified)} fully unclassified"
    )
    return {"summary": summary, "fully_unclassified": fully_unclassified}


def lookup_sponsors(nct_ids: list[str], dsn: str = DEFAULT_DSN) -> dict[str, dict[str, Any]]:
    """sponsor_name/sponsor_class per nct_id, from S1-05's Postgres trials
    table -- required for detection_failures.log's sponsor attribution."""
    if not nct_ids:
        return {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT nct_id, sponsor_name, sponsor_class FROM trials WHERE nct_id = ANY(%s)",
            (nct_ids,),
        )
        return {row[0]: {"sponsor_name": row[1], "sponsor_class": row[2]} for row in cur.fetchall()}


def write_detection_failures_log(
    fully_unclassified: list[dict[str, str]],
    dest_path: Path = DEFAULT_FAILURES_LOG,
    sponsor_lookup: Callable[[list[str]], dict[str, dict[str, Any]]] = lookup_sponsors,
) -> None:
    nct_ids = sorted({entry["nct_id"] for entry in fully_unclassified})
    sponsors = sponsor_lookup(nct_ids)

    lines = []
    for entry in fully_unclassified:
        sponsor = sponsors.get(entry["nct_id"], {})
        lines.append(
            "\t".join(
                [
                    entry["nct_id"],
                    entry["doc_type"],
                    str(sponsor.get("sponsor_name") or "UNKNOWN"),
                    str(sponsor.get("sponsor_class") or "UNKNOWN"),
                ]
            )
        )

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text("\n".join(lines) + ("\n" if lines else ""))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Segment the extracted corpus into canonical sections."
    )
    parser.add_argument("--extracted-dir", type=Path, default=DEFAULT_EXTRACTED_DIR)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_DIR)
    parser.add_argument("--failures-log", type=Path, default=DEFAULT_FAILURES_LOG)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    args = parser.parse_args()

    result = segment_corpus(extracted_dir=args.extracted_dir, dest_dir=args.dest)
    write_detection_failures_log(
        result["fully_unclassified"],
        dest_path=args.failures_log,
        sponsor_lookup=lambda ids: lookup_sponsors(ids, dsn=args.dsn),
    )
    print(
        f"{len(result['fully_unclassified'])} fully-unclassified document(s) -> {args.failures_log}"
    )


if __name__ == "__main__":
    main()
