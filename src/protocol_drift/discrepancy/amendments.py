"""Amendment history extraction -- S4-01 (partial: step 2 only).

`outcome_amendment_events` is the one piece of S4-01 that T4 (S4-02) needs
and that's fully answerable from data Sprint 1 already loaded: Postgres
`amendments` (S1-05's table -- `version`, `date`, `modules_changed`, per
`field_paths.md` SS4's confirmed `moduleLabels` vocabulary) already holds
the real revision history for the full cohort, so filtering it to
outcome-touching rows needs no new fetching.

The rest of S4-01 -- re-verifying the live `.../history` endpoint,
`ensure_version_snapshot`, `diff_outcome_text`, `amendment_timeline`, and
`data/discrepancy/amendment_timelines.json` -- produces the *intermediate*
outcome text at each individual revision, which T4 doesn't need (see
`eval/t4_questions.py`'s module docstring for how it avoids that
dependency) and isn't implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

# Confirmed real value in the loaded cohort's `amendments.modules_changed`
# (per `field_paths.md` SS4) -- the label `history.json`'s `changes[]` uses
# for a revision that touched `protocolSection.outcomesModule`.
OUTCOME_MODULE_LABEL = "Outcome Measures"


@dataclass(frozen=True)
class AmendmentEvent:
    nct_id: str
    version: int
    date: str | None
    modules_changed: list[str]


def outcome_amendment_events(nct_id: str, conn: psycopg.Connection[Any]) -> list[AmendmentEvent]:
    """Every amendment row for `nct_id` whose `modules_changed` includes the
    outcomes label, oldest version first -- the population of "which
    trials, and which specific revisions, actually touched the primary
    outcome" that T4's amendment-aware questions are drawn from."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version, date, modules_changed FROM amendments "
            "WHERE nct_id = %s AND %s = ANY(modules_changed) ORDER BY version",
            (nct_id, OUTCOME_MODULE_LABEL),
        )
        rows = cur.fetchall()
    return [
        AmendmentEvent(nct_id=nct_id, version=version, date=date, modules_changed=list(modules))
        for version, date, modules in rows
    ]
