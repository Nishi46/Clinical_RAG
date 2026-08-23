"""Registry snapshot — archive the frozen gold source to disk.

The live registry mutates (sprint_plan.md risk register: "Registry data
mutates mid-project — Certain / High"). Everything downstream — fact
extraction (S1-05), discrepancy detection (Sprint 4) — must read from the
files this module writes, never re-fetch live.

Per trial, archives:
    current.json      -- full current record (client.get_study, no field restriction)
    history.json       -- revision index (client.get_history)
    versions/0.json     -- first-posted snapshot (client.get_history_version(nct_id, 0))

Scope decision: only version 0 is archived, not every intermediate version.
Version 0 (first-posted) plus current.json (latest) is exactly what the
three-way outcome comparison in field_paths.md needs (registered-first vs.
registered-current vs. results-reported); `history.json`'s `changes[]` +
`moduleLabels` already gives Sprint 4's amendment-tagging work (S4-01) a
per-revision change log without needing every version's full body archived.
Some trials in this cohort have 90+ revisions (see NCT02872116) — archiving
every one for 200 trials would be hundreds of extra unbounded requests
against an unstable, undocumented endpoint for marginal extra value.

Resumable at per-file granularity: an existing file is not re-fetched unless
`force=True`, so a partial or interrupted run can simply be re-invoked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from protocol_drift.registry.client import (
    DEFAULT_BASE_URL,
    DEFAULT_INTERNAL_BASE_URL,
    HistoryEndpointUnavailable,
    RegistryClient,
)

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_SNAPSHOT_DIR = Path("data/registry_snapshots")

FETCH_ERRORS = (requests.RequestException, HistoryEndpointUnavailable)

README_TEXT = """\
# Registry snapshots — frozen gold source

This directory is the frozen registry archive for the Sprint 1 cohort. It is
written once per trial (resumable, not re-fetched once a file exists) and is
the **only** source downstream extraction (S1-05) and discrepancy detection
(Sprint 4) may read from. Never re-fetch these facts live — the live
registry mutates, and unarchived gold is unreproducible gold.

Per trial (`{nct_id}/`):
- `current.json` — full current record
- `history.json` — revision index (`changes[]`, `lastUpdateVersions{}`)
- `versions/0.json` — first-posted snapshot (version 0)

`manifest.json` records, per archived file: source URL, SHA-256 hash, and
fetch timestamp (derived from the file's mtime) — the archive's own
integrity record.
"""


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_files(nct_id: str) -> list[tuple[str, str]]:
    """(relative path, source URL) pairs a fully-archived trial should have."""
    return [
        ("current.json", f"{DEFAULT_BASE_URL}/studies/{nct_id}"),
        ("history.json", f"{DEFAULT_INTERNAL_BASE_URL}/studies/{nct_id}/history"),
        (
            str(Path("versions") / "0.json"),
            f"{DEFAULT_INTERNAL_BASE_URL}/studies/{nct_id}/history/0",
        ),
    ]


def snapshot_trial(
    client: RegistryClient, nct_id: str, dest_dir: Path, force: bool = False
) -> list[str]:
    """Archive one trial's current record, history, and version-0 snapshot.

    Returns a list of error strings (empty if everything succeeded or was
    already archived). Never raises — failures against a batch of 200 live
    network calls are expected and must not abort the run.
    """
    trial_dir = dest_dir / nct_id
    errors: list[str] = []

    current_path = trial_dir / "current.json"
    if force or not current_path.exists():
        try:
            _write_json(current_path, client.get_study(nct_id))
        except FETCH_ERRORS as exc:
            errors.append(f"current: {exc}")

    history_path = trial_dir / "history.json"
    if force or not history_path.exists():
        try:
            _write_json(history_path, client.get_history(nct_id))
        except FETCH_ERRORS as exc:
            errors.append(f"history: {exc}")

    v0_path = trial_dir / "versions" / "0.json"
    if force or not v0_path.exists():
        try:
            _write_json(v0_path, client.get_history_version(nct_id, 0))
        except FETCH_ERRORS as exc:
            errors.append(f"version_0: {exc}")

    return errors


def write_manifest(dest_dir: Path, nct_ids: list[str]) -> None:
    """Rebuild manifest.json from whatever is currently on disk — makes the
    manifest a pure function of archive state, safe to regenerate after any
    partial/resumed run."""
    entries = []
    for nct_id in sorted(nct_ids):
        for rel_path, url in _expected_files(nct_id):
            path = dest_dir / nct_id / rel_path
            if not path.exists():
                continue
            entries.append(
                {
                    "nct_id": nct_id,
                    "file": rel_path,
                    "source_url": url,
                    "sha256": _sha256_file(path),
                    "fetched_at": datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
                }
            )
    manifest = {"entries": entries}
    (dest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def snapshot_cohort(
    client: RegistryClient,
    cohort_path: Path = DEFAULT_COHORT_PATH,
    dest_dir: Path = DEFAULT_SNAPSHOT_DIR,
    force: bool = False,
) -> None:
    cohort = json.loads(cohort_path.read_text())
    nct_ids = [t["nct_id"] for t in cohort["trials"]]

    error_lines: list[str] = []
    for nct_id in nct_ids:
        for err in snapshot_trial(client, nct_id, dest_dir, force=force):
            error_lines.append(f"{nct_id}\t{err}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    errors_path = dest_dir / "fetch_errors.log"
    errors_path.write_text("\n".join(error_lines) + ("\n" if error_lines else ""))

    write_manifest(dest_dir, nct_ids)
    (dest_dir / "README.md").write_text(README_TEXT)

    print(f"Archived {len(nct_ids)} trials -> {dest_dir}")
    if error_lines:
        print(f"{len(error_lines)} fetch error(s) logged to {errors_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive the frozen cohort's registry gold.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--dest", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument(
        "--force", action="store_true", help="re-fetch even if a file already exists"
    )
    args = parser.parse_args()

    client = RegistryClient()
    snapshot_cohort(client, cohort_path=args.cohort, dest_dir=args.dest, force=args.force)


if __name__ == "__main__":
    main()
