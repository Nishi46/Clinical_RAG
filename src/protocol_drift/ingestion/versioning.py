"""Amendment/version tagging -- page-level version markers from running
footers/headers, reconciled against the registry's amendment history.

Distinct from S1-05's Postgres `amendments` table, which is registry-level
version history (`.../history`'s `changes[]` -- one row per API-recorded
revision). This module tags the *document's own* version marker as printed
on the page itself, confirmed real in corpus_assessment.md Sec.6: the
NCT02872116 protocol's running footer reads "Revised Protocol No.: 09" on
essentially every content page (pages 2-170 of 171, 0-indexed) but not on
its title/signature pages (0-1) -- so this has to work at the page level,
not just be one value stamped on the whole document.

Reads only from data/extracted/ (S2-01's blocks, for footer/header text)
plus the original PDFs (for true per-page height -- S2-01's output doesn't
carry page dimensions, only block bboxes) and Postgres (S1-05's amendments
table), never re-extracts.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fitz  # pymupdf
import psycopg

from protocol_drift.db import DEFAULT_DSN

DEFAULT_EXTRACTED_DIR = Path("data/extracted")
DEFAULT_DEST_DIR = Path("data/versions")
DEFAULT_WARNINGS_LOG = Path("data/versions/version_warnings.log")

# Confirmed real (NCT02872116 protocol, pages 2-170): the footer sits at
# 89.5%-92.5% of page height, not within a strict top/bottom 10% band --
# 12% is the smallest round margin that reliably covers it without also
# swallowing ordinary body text near mid-page.
HEADER_FOOTER_MARGIN_FRACTION = 0.12

# A real footer/header marker is a short running line ("Revised Protocol
# No.: 09 Date: 16-Sep-2019 85"), never a full paragraph -- confirmed real
# false positive without this filter: a body paragraph ending "...SAS for
# Windows, version 9.4, Cary, NC." can itself start low enough on a
# lightly-filled page to fall inside the bottom margin band by y0 alone.
FOOTER_MAX_BLOCK_CHARS = 150

# Below this fraction of a document's pages carrying a detected marker, its
# versioning is unreliable enough to flag -- expected on thinner/academic
# documents (corpus_assessment.md Sec.4) that simply don't print a running
# version footer at all.
LOW_COVERAGE_THRESHOLD = 0.5

PAGE_ERROR_TYPES = (fitz.mupdf.FzErrorBase, RuntimeError, IndexError, ValueError)

# S2-01's block text joins spans with no separator, so a line break with no
# trailing space in the source PDF can fuse a real marker straight onto an
# adjacent date with zero delimiter -- confirmed real (NCT03056755): "SAP
# Amendment 3" + "24-Mar-2022" renders as "...Amendment 324-Mar-2022",
# which a bare \d+ would misread as amendment 324. These lookaheads reject
# a digit run immediately followed by a slash-separated date ("04" of
# "04/12/2019") or an unspaced "DD-Mon-YYYY" continuation ("24-Mar-2022").
# The digit group is wrapped in an atomic group `(?>...)` so a rejected
# full-length match (e.g. "324") never backtracks into a shorter, equally
# wrong one ("32") -- the page correctly comes back with no marker at all
# rather than a differently wrong one. This can't use a plain `\b` after
# the digits instead: real footer text routinely has no space between a
# marker and the word right after it (confirmed real: "09Date:..."), and
# `\b` does not hold between a digit and a following letter (both \w).
_NOT_FOLLOWED_BY_DATE = r"(?!\s*[/-]\d)(?!-[A-Za-z]{3}-\d)"

# Checked in this order; first match wins. Patterns operate on footer/header
# band text only (see _header_footer_text), never full-page text, so a body
# paragraph that happens to mention "Amendment 12" in prose doesn't get
# mistaken for the page's own version marker.
VERSION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "revised_protocol",
        re.compile(
            r"revised\s+protocol\s+(?:no\.?|number)\s*:?\s*(?>(\d+))" + _NOT_FOLLOWED_BY_DATE,
            re.IGNORECASE,
        ),
    ),
    (
        "amendment",
        re.compile(r"\bamendment\s*#?\s*(?>(\d+))" + _NOT_FOLLOWED_BY_DATE, re.IGNORECASE),
    ),
    (
        "version",
        re.compile(
            # (?!\.\d) additionally rejects a dotted match that's really a
            # third date segment away from being one -- confirmed real
            # (NCT03069313): "Version: 02.19.16" (Feb 19 2016, dot-separated
            # MM.DD.YY) would otherwise mis-extract "02.19" as version 2.19.
            r"\bversion\s*:?\s*(?>(\d+(?:\.\d+)?))" + _NOT_FOLLOWED_BY_DATE + r"(?!\.\d)",
            re.IGNORECASE,
        ),
    ),
]

DATE_PATTERN = re.compile(r"date\s*:?\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", re.IGNORECASE)

# A bare "version N[.N]" is ambiguous: SAP footers/headers routinely name
# their analysis software's own version right next to the document's own
# (e.g. confirmed real, NCT03040115/NCT03085238's SAPs: "...using SAS
# version 9.4"), which isn't a document revision at all. Only the
# "version" pattern is ambiguous this way -- "Revised Protocol No." and
# "Amendment" have no comparable software-version idiom -- so this check
# is scoped to that one pattern rather than applied everywhere.
SOFTWARE_PRECEDER_WINDOW = 15
SOFTWARE_PRECEDER = re.compile(r"\b(?:sas|spss|stata)\b.{0,3}$", re.IGNORECASE)


def extract_page_version_marker(page_text: str) -> dict[str, Any] | None:
    """First matching version pattern in already-isolated footer/header
    text, or None. A dotted match ("Version 2.1") keeps its fractional
    value; whole-number matches ("Revised Protocol No.: 09", "Amendment 3")
    parse as int, so a leading zero like "09" doesn't leak into
    reconciliation as a string."""
    for pattern_name, pattern in VERSION_PATTERNS:
        match = pattern.search(page_text)
        if not match:
            continue
        if pattern_name == "version":
            preceding = page_text[max(0, match.start() - SOFTWARE_PRECEDER_WINDOW) : match.start()]
            if SOFTWARE_PRECEDER.search(preceding):
                continue
        raw_version = match.group(1)
        version: int | float = float(raw_version) if "." in raw_version else int(raw_version)
        date_match = DATE_PATTERN.search(page_text)
        return {
            "version": version,
            "raw_version": raw_version,
            "date": date_match.group(1) if date_match else None,
            "pattern": pattern_name,
        }
    return None


def _header_footer_text(page_record: dict[str, Any], page_height: float) -> str:
    if page_height <= 0:
        return ""
    margin = HEADER_FOOTER_MARGIN_FRACTION * page_height
    band = [
        b["text"]
        for b in page_record["blocks"]
        if len(b["text"]) <= FOOTER_MAX_BLOCK_CHARS
        and (b["bbox"][1] < margin or b["bbox"][1] > page_height - margin)
    ]
    return " ".join(band)


def document_version_timeline(
    document_content: dict[str, Any], pdf_path: Path | None = None
) -> list[tuple[list[int], dict[str, Any] | None]]:
    """Per-page version markers collapsed into contiguous same-version page
    ranges. True page height isn't in S2-01's output (only block bboxes
    are), so this reopens the source PDF -- same tradeoff sections.py makes
    for get_toc() -- rather than guessing page height from the blocks
    themselves, which can vary within one document (this corpus mixes
    Letter and Legal page sizes even inside a single PDF)."""
    resolved_pdf_path = pdf_path or Path(document_content["source_path"])
    doc = fitz.open(resolved_pdf_path)
    try:
        heights = [doc[i].rect.height for i in range(doc.page_count)]
    finally:
        doc.close()

    markers: list[dict[str, Any] | None] = []
    for page in document_content["pages"]:
        height = heights[page["page_number"]] if page["page_number"] < len(heights) else 0.0
        text = _header_footer_text(page, height)
        markers.append(extract_page_version_marker(text))

    if not markers:
        return []

    timeline: list[tuple[list[int], dict[str, Any] | None]] = []
    run_start = 0
    run_marker = markers[0]
    for i in range(1, len(markers) + 1):
        current = markers[i] if i < len(markers) else None
        both_none = current is None and run_marker is None
        both_same_version = (
            current is not None
            and run_marker is not None
            and current["version"] == run_marker["version"]
        )
        same = i < len(markers) and (both_none or both_same_version)
        if not same:
            timeline.append(([run_start, i - 1], run_marker))
            if i < len(markers):
                run_start = i
                run_marker = current
    return timeline


def mark_superseded(
    timeline: list[tuple[list[int], dict[str, Any] | None]],
) -> list[dict[str, Any]]:
    """Version records for the timeline, with superseded=True only for a
    page range whose marker is a strictly older version number than the
    document's own max detected version -- never inferred from anything
    softer than that explicit mismatch (e.g. a page with no marker at all
    is left superseded=False, not assumed old)."""
    versions = [m["version"] for _, m in timeline if m is not None]
    max_version = max(versions) if versions else None

    return [
        {
            "page_range": page_range,
            "version_marker": marker,
            "superseded": bool(
                marker is not None and max_version is not None and marker["version"] < max_version
            ),
        }
        for page_range, marker in timeline
    ]


def lookup_registry_amendments(
    nct_ids: list[str], dsn: str = DEFAULT_DSN
) -> dict[str, list[dict[str, Any]]]:
    """S1-05's Postgres amendments rows (registry-reported revision history)
    per nct_id -- the reconciliation target, not the source of truth on its
    own (see reconcile_with_registry)."""
    if not nct_ids:
        return {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT nct_id, version, date, modules_changed FROM amendments "
            "WHERE nct_id = ANY(%s) ORDER BY nct_id, version",
            (nct_ids,),
        )
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for nct_id, version, date, modules_changed in cur.fetchall():
            result[nct_id].append(
                {"version": version, "date": date, "modules_changed": modules_changed}
            )
    return dict(result)


def reconcile_with_registry(
    doc_versions: list[tuple[list[int], dict[str, Any] | None]],
    registry_amendments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Flags agreement/mismatch/unresolvable between the document's own
    detected version marker(s) and the registry's amendment history, rather
    than trusting either alone. The two numbering schemes aren't the same
    thing -- the registry's `version` is a sequential per-API-revision
    counter (confirmed real: NCT02872116 has 92 such revisions, numbered
    0-91, versus its own footer's single "Amendment 09"), so this can only
    check whether the document's claimed version is *plausible* against the
    registry's observed range, not that they name the same real-world
    event. A document version above the registry's max is a genuine red
    flag (it claims to be a later revision than the registry has ever
    recorded); unresolvable only when one side has nothing to compare."""
    doc_version_numbers = sorted({m["version"] for _, m in doc_versions if m is not None})
    registry_version_numbers = sorted({row["version"] for row in registry_amendments})

    if not doc_version_numbers:
        return {
            "status": "unresolvable",
            "doc_version": None,
            "registry_version_range": [registry_version_numbers[0], registry_version_numbers[-1]]
            if registry_version_numbers
            else None,
            "detail": "no document version marker detected",
        }
    if not registry_version_numbers:
        return {
            "status": "unresolvable",
            "doc_version": doc_version_numbers[-1],
            "registry_version_range": None,
            "detail": "no registry amendment rows for this trial",
        }

    max_doc_version = doc_version_numbers[-1]
    registry_range = [registry_version_numbers[0], registry_version_numbers[-1]]
    if registry_range[0] <= max_doc_version <= registry_range[1]:
        status = "agreement"
        detail = (
            f"document version {max_doc_version} falls within "
            f"registry's observed range {registry_range}"
        )
    else:
        status = "mismatch"
        detail = (
            f"document version {max_doc_version} falls outside "
            f"registry's observed range {registry_range}"
        )

    return {
        "status": status,
        "doc_version": max_doc_version,
        "registry_version_range": registry_range,
        "detail": detail,
    }


def _coverage(timeline: list[tuple[list[int], dict[str, Any] | None]], total_pages: int) -> float:
    if total_pages == 0:
        return 0.0
    covered = sum(end - start + 1 for (start, end), marker in timeline if marker is not None)
    return covered / total_pages


def extracted_documents(extracted_dir: Path = DEFAULT_EXTRACTED_DIR) -> list[Path]:
    return sorted(extracted_dir.glob("*/*.json"))


RegistryLookup = Callable[[list[str]], dict[str, list[dict[str, Any]]]]


def version_corpus(
    extracted_dir: Path = DEFAULT_EXTRACTED_DIR,
    dest_dir: Path = DEFAULT_DEST_DIR,
    registry_lookup: RegistryLookup = lookup_registry_amendments,
) -> dict[str, Any]:
    paths = extracted_documents(extracted_dir)
    documents_content = [json.loads(p.read_text()) for p in paths]
    nct_ids = sorted({d["nct_id"] for d in documents_content})
    registry_by_trial = registry_lookup(nct_ids)

    documents = 0
    low_coverage: list[dict[str, str]] = []
    errors: list[str] = []

    for path, content in zip(paths, documents_content, strict=True):
        try:
            timeline = document_version_timeline(content, pdf_path=Path(content["source_path"]))
        except PAGE_ERROR_TYPES as exc:
            errors.append(f"{content['nct_id']}\t{content['doc_type']}\t{exc}")
            continue

        records = mark_superseded(timeline)
        reconciliation = reconcile_with_registry(
            timeline, registry_by_trial.get(content["nct_id"], [])
        )

        dest_path = dest_dir / content["nct_id"] / path.name
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(
            json.dumps(
                {
                    "nct_id": content["nct_id"],
                    "doc_type": content["doc_type"],
                    "versions": records,
                    "reconciliation": reconciliation,
                },
                indent=2,
            )
            + "\n"
        )
        documents += 1

        if _coverage(timeline, content["total_pages"]) < LOW_COVERAGE_THRESHOLD:
            low_coverage.append({"nct_id": content["nct_id"], "doc_type": content["doc_type"]})

    if low_coverage:
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "version_warnings.log").write_text(
            "\n".join(f"{d['nct_id']}\t{d['doc_type']}" for d in low_coverage) + "\n"
        )
    if errors:
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "versioning_errors.log").write_text("\n".join(errors) + "\n")

    summary = {
        "documents": documents,
        "low_coverage": len(low_coverage),
        "failed": len(errors),
    }
    print(
        f"Versioned {documents} document(s): {len(low_coverage)} with sparse/absent markers, "
        f"{len(errors)} failed"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract page-level version markers and reconcile against the registry."
    )
    parser.add_argument("--extracted-dir", type=Path, default=DEFAULT_EXTRACTED_DIR)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_DIR)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    args = parser.parse_args()

    version_corpus(
        extracted_dir=args.extracted_dir,
        dest_dir=args.dest,
        registry_lookup=lambda ids: lookup_registry_amendments(ids, dsn=args.dsn),
    )


if __name__ == "__main__":
    main()
