"""Registry fact extraction — data/registry_snapshots/ -> Postgres.

Reads exclusively from the frozen archive S1-04 wrote (current.json,
history.json, versions/0.json per trial) and never the live API — this is
downstream of the "unarchived gold is unreproducible gold" rule.

Field paths below are the ones confirmed in scratch/field_paths.md and
cross-checked against the real archived fixture (NCT02872116, CheckMate-649).
`outcomes.source` implements the three-way comparison design that's the
project's headline capability: 'registered_first' (from versions/0.json),
'registered_current' (from current.json's protocolSection), and
'results_reported' (from current.json's resultsSection, present only when
hasResults is true).

The load is idempotent: each trial's rows are deleted and re-inserted (or
upserted, for the two tables with a natural key), so re-running against an
unchanged archive is a no-op in effect and re-running after an archive
update reflects it cleanly.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_SNAPSHOT_DIR = Path("data/registry_snapshots")
DEFAULT_DSN = "dbname=protocol_drift_dev"

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def extract_trial_row(current: dict[str, Any]) -> dict[str, Any]:
    ps = current.get("protocolSection", {})
    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    sponsor = ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    design = ps.get("designModule", {})
    conditions = ps.get("conditionsModule", {}).get("conditions", [])
    docs = current.get("documentSection", {}).get("largeDocumentModule", {}).get("largeDocs", [])
    return {
        "nct_id": ident["nctId"],
        "brief_title": ident.get("briefTitle", ""),
        "condition": "|".join(conditions) if conditions else None,
        "phase": "|".join(design.get("phases", [])) or None,
        "sponsor_class": sponsor.get("class"),
        "sponsor_name": sponsor.get("name"),
        "overall_status": status.get("overallStatus"),
        "start_date": (status.get("startDateStruct") or {}).get("date"),
        "primary_completion_date": (status.get("primaryCompletionDateStruct") or {}).get("date"),
        "has_protocol": any(d.get("hasProtocol") for d in docs),
        "has_sap": any(d.get("hasSap") for d in docs),
    }


def _protocol_outcomes(protocol_section: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    outcomes_module = protocol_section.get("outcomesModule", {})
    rows: list[tuple[str, dict[str, Any]]] = []
    rows += [("PRIMARY", o) for o in outcomes_module.get("primaryOutcomes", [])]
    rows += [("SECONDARY", o) for o in outcomes_module.get("secondaryOutcomes", [])]
    return rows


def extract_outcomes(current: dict[str, Any], v0_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    nct_id = current["protocolSection"]["identificationModule"]["nctId"]
    rows: list[dict[str, Any]] = []

    for kind, o in _protocol_outcomes(current.get("protocolSection", {})):
        rows.append(
            {
                "nct_id": nct_id,
                "kind": kind,
                "source": "registered_current",
                "measure": o.get("measure", ""),
                "timeframe": o.get("timeFrame"),
                "description": o.get("description"),
                "version": None,
            }
        )

    v0_study = v0_snapshot.get("study", {})
    for kind, o in _protocol_outcomes(v0_study.get("protocolSection", {})):
        rows.append(
            {
                "nct_id": nct_id,
                "kind": kind,
                "source": "registered_first",
                "measure": o.get("measure", ""),
                "timeframe": o.get("timeFrame"),
                "description": o.get("description"),
                "version": 0,
            }
        )

    results_measures = (
        current.get("resultsSection", {})
        .get("outcomeMeasuresModule", {})
        .get("outcomeMeasures", [])
    )
    for o in results_measures:
        rows.append(
            {
                "nct_id": nct_id,
                "kind": o.get("type", "UNKNOWN"),
                "source": "results_reported",
                "measure": o.get("title", ""),
                "timeframe": o.get("timeFrame"),
                "description": o.get("description"),
                "version": None,
            }
        )

    return rows


def extract_arms(current: dict[str, Any]) -> list[dict[str, Any]]:
    nct_id = current["protocolSection"]["identificationModule"]["nctId"]
    arm_groups = (
        current.get("protocolSection", {}).get("armsInterventionsModule", {}).get("armGroups", [])
    )
    return [
        {
            "nct_id": nct_id,
            "arm_label": a.get("label", ""),
            "arm_type": a.get("type"),
            "description": a.get("description"),
        }
        for a in arm_groups
    ]


def extract_eligibility(current: dict[str, Any]) -> dict[str, Any] | None:
    nct_id = current["protocolSection"]["identificationModule"]["nctId"]
    elig = current.get("protocolSection", {}).get("eligibilityModule", {})
    if not elig:
        return None
    return {
        "nct_id": nct_id,
        "min_age": elig.get("minimumAge"),
        "max_age": elig.get("maximumAge"),
        "sex": elig.get("sex"),
        "criteria_text": elig.get("eligibilityCriteria"),
    }


def extract_amendments(nct_id: str, history: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "nct_id": nct_id,
            "version": c["version"],
            "date": c.get("date"),
            "modules_changed": c.get("moduleLabels", []),
        }
        for c in history.get("changes", [])
    ]


_UPSERT_TRIAL = """
INSERT INTO trials (
    nct_id, brief_title, condition, phase, sponsor_class, sponsor_name,
    overall_status, start_date, primary_completion_date, has_protocol, has_sap
) VALUES (
    %(nct_id)s, %(brief_title)s, %(condition)s, %(phase)s, %(sponsor_class)s,
    %(sponsor_name)s, %(overall_status)s, %(start_date)s,
    %(primary_completion_date)s, %(has_protocol)s, %(has_sap)s
)
ON CONFLICT (nct_id) DO UPDATE SET
    brief_title = EXCLUDED.brief_title,
    condition = EXCLUDED.condition,
    phase = EXCLUDED.phase,
    sponsor_class = EXCLUDED.sponsor_class,
    sponsor_name = EXCLUDED.sponsor_name,
    overall_status = EXCLUDED.overall_status,
    start_date = EXCLUDED.start_date,
    primary_completion_date = EXCLUDED.primary_completion_date,
    has_protocol = EXCLUDED.has_protocol,
    has_sap = EXCLUDED.has_sap
"""

_UPSERT_ELIGIBILITY = """
INSERT INTO eligibility (nct_id, min_age, max_age, sex, criteria_text)
VALUES (%(nct_id)s, %(min_age)s, %(max_age)s, %(sex)s, %(criteria_text)s)
ON CONFLICT (nct_id) DO UPDATE SET
    min_age = EXCLUDED.min_age,
    max_age = EXCLUDED.max_age,
    sex = EXCLUDED.sex,
    criteria_text = EXCLUDED.criteria_text
"""

_INSERT_OUTCOME = """
INSERT INTO outcomes (nct_id, kind, source, measure, timeframe, description, version)
VALUES (%(nct_id)s, %(kind)s, %(source)s, %(measure)s, %(timeframe)s, %(description)s, %(version)s)
"""

_INSERT_ARM = """
INSERT INTO arms (nct_id, arm_label, arm_type, description)
VALUES (%(nct_id)s, %(arm_label)s, %(arm_type)s, %(description)s)
"""

_INSERT_AMENDMENT = """
INSERT INTO amendments (nct_id, version, date, modules_changed)
VALUES (%(nct_id)s, %(version)s, %(date)s, %(modules_changed)s)
"""


def load_trial(cur: psycopg.Cursor[Any], nct_id: str, trial_dir: Path) -> None:
    current = json.loads((trial_dir / "current.json").read_text())
    v0 = json.loads((trial_dir / "versions" / "0.json").read_text())
    history_path = trial_dir / "history.json"
    history = json.loads(history_path.read_text()) if history_path.exists() else {"changes": []}

    # Idempotent re-run: clear this trial's child rows before re-inserting.
    cur.execute("DELETE FROM outcomes WHERE nct_id = %s", (nct_id,))
    cur.execute("DELETE FROM arms WHERE nct_id = %s", (nct_id,))
    cur.execute("DELETE FROM amendments WHERE nct_id = %s", (nct_id,))

    cur.execute(_UPSERT_TRIAL, extract_trial_row(current))

    eligibility = extract_eligibility(current)
    if eligibility is not None:
        cur.execute(_UPSERT_ELIGIBILITY, eligibility)

    outcomes = extract_outcomes(current, v0)
    if outcomes:
        cur.executemany(_INSERT_OUTCOME, outcomes)

    arms = extract_arms(current)
    if arms:
        cur.executemany(_INSERT_ARM, arms)

    amendments = extract_amendments(nct_id, history)
    if amendments:
        cur.executemany(_INSERT_AMENDMENT, amendments)


def load_cohort_into_db(
    cohort_path: Path,
    snapshot_dir: Path,
    conn: psycopg.Connection[Any],
) -> int:
    cohort = json.loads(cohort_path.read_text())
    nct_ids = [t["nct_id"] for t in cohort["trials"]]

    with conn.cursor() as cur:
        for nct_id in nct_ids:
            load_trial(cur, nct_id, snapshot_dir / nct_id)
    conn.commit()
    return len(nct_ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load frozen registry gold into Postgres.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument(
        "--apply-schema",
        action="store_true",
        help="run db/schema.sql before loading (safe to repeat: CREATE TABLE IF NOT EXISTS)",
    )
    args = parser.parse_args()

    conn = psycopg.connect(args.dsn)
    if args.apply_schema:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text())
        conn.commit()

    count = load_cohort_into_db(args.cohort, args.snapshot_dir, conn)
    print(f"Loaded {count} trials into {args.dsn}")


if __name__ == "__main__":
    main()
