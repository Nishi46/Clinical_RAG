import json
from pathlib import Path
from typing import Any

import pytest
import requests

from protocol_drift.registry.client import HistoryEndpointUnavailable
from protocol_drift.registry.snapshot import (
    snapshot_cohort,
    snapshot_trial,
    write_manifest,
)


class _StubClient:
    """Stands in for RegistryClient without touching the network. Each
    fetch method either returns a canned payload or raises, per the flags
    passed at construction — mirrors the real client's exception surface
    (RequestException from get_study, HistoryEndpointUnavailable from the
    two history endpoints)."""

    def __init__(
        self,
        fail_current: bool = False,
        fail_history: bool = False,
        fail_version: bool = False,
    ) -> None:
        self.fail_current = fail_current
        self.fail_history = fail_history
        self.fail_version = fail_version
        self.calls: list[str] = []

    def get_study(self, nct_id: str) -> dict[str, Any]:
        self.calls.append(f"get_study:{nct_id}")
        if self.fail_current:
            raise requests.RequestException("boom")
        return {"protocolSection": {"identificationModule": {"nctId": nct_id}}}

    def get_history(self, nct_id: str) -> dict[str, Any]:
        self.calls.append(f"get_history:{nct_id}")
        if self.fail_history:
            raise HistoryEndpointUnavailable("boom")
        return {"changes": [{"version": 0}], "lastUpdateVersions": {}}

    def get_history_version(self, nct_id: str, version: int) -> dict[str, Any]:
        self.calls.append(f"get_history_version:{nct_id}:{version}")
        if self.fail_version:
            raise HistoryEndpointUnavailable("boom")
        return {"studyVersion": version, "study": {}}


def test_snapshot_trial_writes_three_files(tmp_path: Path) -> None:
    client = _StubClient()
    errors = snapshot_trial(client, "NCT00000001", tmp_path)

    assert errors == []
    assert (tmp_path / "NCT00000001" / "current.json").exists()
    assert (tmp_path / "NCT00000001" / "history.json").exists()
    assert (tmp_path / "NCT00000001" / "versions" / "0.json").exists()


def test_snapshot_trial_skips_existing_files_unless_forced(tmp_path: Path) -> None:
    client = _StubClient()
    snapshot_trial(client, "NCT00000001", tmp_path)
    assert len(client.calls) == 3

    # second call: nothing missing, so no network calls at all
    snapshot_trial(client, "NCT00000001", tmp_path)
    assert len(client.calls) == 3

    # force=True re-fetches everything regardless of what's on disk
    snapshot_trial(client, "NCT00000001", tmp_path, force=True)
    assert len(client.calls) == 6


def test_snapshot_trial_resumes_partial_state(tmp_path: Path) -> None:
    # current.json already present; history/version missing -- only the
    # missing pieces should be fetched.
    trial_dir = tmp_path / "NCT00000001"
    trial_dir.mkdir(parents=True)
    (trial_dir / "current.json").write_text('{"already": "here"}')

    client = _StubClient()
    snapshot_trial(client, "NCT00000001", tmp_path)

    assert client.calls == ["get_history:NCT00000001", "get_history_version:NCT00000001:0"]
    # pre-existing file is untouched
    assert json.loads((trial_dir / "current.json").read_text()) == {"already": "here"}


def test_snapshot_trial_logs_errors_without_raising(tmp_path: Path) -> None:
    client = _StubClient(fail_current=True, fail_version=True)
    errors = snapshot_trial(client, "NCT00000001", tmp_path)

    assert len(errors) == 2
    assert any("current" in e for e in errors)
    assert any("version_0" in e for e in errors)
    # history succeeded despite the other two failing
    assert (tmp_path / "NCT00000001" / "history.json").exists()
    assert not (tmp_path / "NCT00000001" / "current.json").exists()


def test_write_manifest_reflects_disk_state(tmp_path: Path) -> None:
    client = _StubClient(fail_version=True)
    snapshot_trial(client, "NCT00000001", tmp_path)

    write_manifest(tmp_path, ["NCT00000001"])
    manifest = json.loads((tmp_path / "manifest.json").read_text())

    files = {e["file"] for e in manifest["entries"]}
    assert files == {"current.json", "history.json"}  # version_0 failed, absent
    for entry in manifest["entries"]:
        assert len(entry["sha256"]) == 64
        assert entry["nct_id"] == "NCT00000001"


def test_snapshot_cohort_writes_manifest_readme_and_error_log(tmp_path: Path) -> None:
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(
        json.dumps({"trials": [{"nct_id": "NCT00000001"}, {"nct_id": "NCT00000002"}]})
    )
    dest_dir = tmp_path / "snapshots"
    client = _StubClient(fail_history=True)

    snapshot_cohort(client, cohort_path=cohort_path, dest_dir=dest_dir)

    assert (dest_dir / "manifest.json").exists()
    assert (dest_dir / "README.md").exists()
    error_log = (dest_dir / "fetch_errors.log").read_text()
    assert error_log.count("history:") == 2


@pytest.mark.parametrize("force", [False, True])
def test_snapshot_trial_never_raises_on_client_failure(tmp_path: Path, force: bool) -> None:
    client = _StubClient(fail_current=True, fail_history=True, fail_version=True)
    errors = snapshot_trial(client, "NCT00000001", tmp_path, force=force)
    assert len(errors) == 3
