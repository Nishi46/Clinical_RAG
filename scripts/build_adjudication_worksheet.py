#!/usr/bin/env python3
"""S4-07 step 3 tooling: builds a blind adjudication worksheet from
`data/eval/t3.jsonl`, pulling each row's real source texts straight from
Postgres -- `outcomes` for the two registry legs and results, `chunks` for
the protocol-side excerpt.

**This module never imports `protocol_drift.discrepancy` or reads
`data/discrepancy/reports/`.** That's deliberate, not an oversight: per
`sprint_4_implementation.md` S4-07 step 3, blindness to the detector's own
output has to be enforced mechanically, not just by discipline ("the
risk register's Critical-impact risk"). There is no import path from this
file to the detector's verdicts -- a human filling in this worksheet
cannot see what S4-05 concluded even if they wanted to.

For the protocol leg, this pulls more than S4-05's own narrow
`section='objectives'` retrieval does (`objectives` + `synopsis`, capped),
specifically so a human labeler has a real chance to find text the
automated pipeline missed -- catching that gap is exactly the kind of
error a blind human check is for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg

from protocol_drift.db import DEFAULT_DSN

DEFAULT_T3_PATH = Path("data/eval/t3.jsonl")
DEFAULT_WORKSHEET_PATH = Path("data/eval/t3_adjudication_worksheet.jsonl")

# Protocol section labels worth showing a human labeler for the
# current_vs_protocol pair -- 'objectives' is S2-04's canonical label for
# where a primary endpoint statement lives; 'synopsis' often restates it
# concisely and independently, a useful cross-check.
_PROTOCOL_SECTIONS = ("objectives", "synopsis")
_MAX_PROTOCOL_EXCERPTS = 8


def _fetch_outcome(
    conn: psycopg.Connection[Any], nct_id: str, source: str
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT measure, timeframe, description FROM outcomes WHERE nct_id = %s "
            "AND kind = 'PRIMARY' AND source = %s ORDER BY id LIMIT 1",
            (nct_id, source),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"measure": row[0], "timeframe": row[1], "description": row[2]}


def _fetch_protocol_excerpts(conn: psycopg.Connection[Any], nct_id: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_id, section, page_range, text FROM chunks "
            "WHERE nct_id = %s AND doc_type = 'protocol' AND section = ANY(%s) "
            "ORDER BY chunk_id LIMIT %s",
            (nct_id, list(_PROTOCOL_SECTIONS), _MAX_PROTOCOL_EXCERPTS),
        )
        rows = cur.fetchall()
    return [
        {"chunk_id": chunk_id, "section": section, "page_range": page_range, "text": text}
        for chunk_id, section, page_range, text in rows
    ]


def _fetch_available_protocol_sections(conn: psycopg.Connection[Any], nct_id: str) -> list[str]:
    """Every distinct section label present in this trial's protocol doc
    -- shown only when no objectives/synopsis excerpt was found, so a
    human labeler can tell "genuinely not in this document" apart from
    "might be under a section label this taxonomy doesn't cover"."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT section FROM chunks WHERE nct_id = %s AND doc_type = 'protocol' "
            "ORDER BY section",
            (nct_id,),
        )
        return [row[0] for row in cur.fetchall() if row[0] is not None]


def build_worksheet_row(conn: psycopg.Connection[Any], question: dict[str, Any]) -> dict[str, Any]:
    nct_id = question["nct_id"]
    pair = question["pair"]

    sources: dict[str, Any] = {}
    if pair in ("first_posted_vs_current",):
        sources["registered_first"] = _fetch_outcome(conn, nct_id, "registered_first")
        sources["registered_current"] = _fetch_outcome(conn, nct_id, "registered_current")
    elif pair == "current_vs_protocol":
        sources["registered_current"] = _fetch_outcome(conn, nct_id, "registered_current")
        excerpts = _fetch_protocol_excerpts(conn, nct_id)
        sources["protocol_excerpts"] = excerpts
        sources["protocol_available_sections"] = (
            _fetch_available_protocol_sections(conn, nct_id) if not excerpts else []
        )
    elif pair == "registry_vs_results":
        sources["registered_current"] = _fetch_outcome(conn, nct_id, "registered_current")
        sources["registered_first"] = _fetch_outcome(conn, nct_id, "registered_first")
        sources["results_reported"] = _fetch_outcome(conn, nct_id, "results_reported")

    return {
        "question_id": question["question_id"],
        "nct_id": nct_id,
        "pair": pair,
        "question_text": question["question_text"],
        "sources": sources,
        # Blank fields for the human labeler to fill in. verdict must
        # become one of match/divergence/ambiguous (discrepancy_definition.md
        # SS3, operationalized in docs/adjudication_rubric.md) before this
        # row can be exported to t3_gold_labels.jsonl.
        "label": {"verdict": None, "justification": "", "time_spent_seconds": None},
    }


def build_worksheet(
    t3_path: Path, conn: psycopg.Connection[Any], output_path: Path
) -> list[dict[str, Any]]:
    questions = [json.loads(line) for line in t3_path.read_text().splitlines() if line.strip()]
    rows = [build_worksheet_row(conn, q) for q in questions]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return rows


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Build a blind adjudication worksheet (S4-07) from t3.jsonl -- pulls real source "
            "texts from Postgres only, never touches detector output."
        )
    )
    parser.add_argument("--t3", type=Path, default=DEFAULT_T3_PATH)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--output", type=Path, default=DEFAULT_WORKSHEET_PATH)
    args = parser.parse_args()

    conn = psycopg.connect(args.dsn)
    rows = build_worksheet(args.t3, conn, args.output)

    n_missing_protocol = sum(
        1
        for r in rows
        if r["pair"] == "current_vs_protocol" and not r["sources"].get("protocol_excerpts")
    )
    print(f"Wrote {len(rows)} worksheet row(s) -> {args.output}")
    print(
        f"{n_missing_protocol} current_vs_protocol row(s) have no objectives/synopsis excerpt "
        "-- labeler should check protocol_available_sections before concluding retrieval failed"
    )
    conn.close()


if __name__ == "__main__":
    main()
