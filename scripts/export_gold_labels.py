#!/usr/bin/env python3
"""S4-07 step 4/5 tooling: validates a filled-in adjudication worksheet
(`data/eval/t3_adjudication_worksheet.jsonl`, from
`scripts/build_adjudication_worksheet.py`) and exports the labeled rows to
`data/eval/t3_gold_labels.jsonl` -- kept deliberately separate from
`t3.jsonl`, per S4-06 step 3, so the file holding questions and the file
holding labels can never be the same file a script could accidentally
cross-contaminate.

Only rows with a filled-in `label.verdict` are exported. Per spec step 5
("if short of 60, cut to whatever whole number is labeled rather than
rushing the remainder"), a partially-completed worksheet is valid input --
this script reports the honest count and total time spent, it does not
require every row to be done.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_WORKSHEET_PATH = Path("data/eval/t3_adjudication_worksheet.jsonl")
DEFAULT_OUTPUT_PATH = Path("data/eval/t3_gold_labels.jsonl")

_VALID_VERDICTS = ("match", "divergence", "ambiguous")


def load_worksheet(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def export_gold_labels(rows: list[dict[str, Any]], output_path: Path) -> dict[str, Any]:
    labeled = []
    skipped = 0
    total_seconds = 0.0
    for row in rows:
        label = row.get("label", {})
        verdict = label.get("verdict")
        if verdict is None:
            skipped += 1
            continue
        if verdict not in _VALID_VERDICTS:
            raise ValueError(
                f"{row['question_id']}: verdict {verdict!r} is not one of {_VALID_VERDICTS}"
            )
        total_seconds += label.get("time_spent_seconds") or 0.0
        labeled.append(
            {
                "nct_id": row["nct_id"],
                "pair": row["pair"],
                "verdict": verdict,
                "justification": label.get("justification", ""),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for row in labeled:
            f.write(json.dumps(row) + "\n")

    return {
        "labeled": len(labeled),
        "skipped": skipped,
        "total_hours": total_seconds / 3600,
        "distinct_trials": len({r["nct_id"] for r in labeled}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a filled-in adjudication worksheet to t3_gold_labels.jsonl."
    )
    parser.add_argument("--worksheet", type=Path, default=DEFAULT_WORKSHEET_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    rows = load_worksheet(args.worksheet)
    summary = export_gold_labels(rows, args.output)

    print(
        f"Exported {summary['labeled']} labeled row(s) across {summary['distinct_trials']} "
        f"trial(s) -> {args.output} ({summary['skipped']} row(s) still unlabeled)"
    )
    print(f"Total time logged: {summary['total_hours']:.2f}h")
    if summary["labeled"] < 40:
        print(
            f"WARNING: {summary['labeled']} labeled rows is below the 40-row floor "
            "sprint_4_implementation.md's Done-when criterion asks for."
        )
    if summary["total_hours"] > 5:
        print(
            f"NOTE: {summary['total_hours']:.2f}h exceeds the 5-hour budget -- per spec step 5, "
            "this should have stopped at the 5-hour mark; report the actual n and hours honestly "
            "in docs/retros/ rather than continuing further."
        )


if __name__ == "__main__":
    main()
