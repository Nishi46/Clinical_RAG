import json
import random
from pathlib import Path
from typing import Any

import pytest
import responses

from protocol_drift.registry.client import RegistryClient
from protocol_drift.registry.cohort import (
    candidate_query,
    extract_candidate,
    fetch_candidates,
    has_required_doc,
    select_cohort,
    stratification_summary,
    write_cohort_manifest,
)


def _study(
    nct_id: str,
    sponsor_name: str = "Big Pharma",
    sponsor_class: str = "INDUSTRY",
    phases: list[str] | None = None,
    has_protocol: bool = True,
    has_sap: bool = False,
) -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id},
            "statusModule": {
                "overallStatus": "COMPLETED",
                "studyFirstPostDateStruct": {"date": "2018-01-01"},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": sponsor_name, "class": sponsor_class}
            },
            "designModule": {"phases": phases if phases is not None else ["PHASE3"]},
        },
        "documentSection": {
            "largeDocumentModule": {"largeDocs": [{"hasProtocol": has_protocol, "hasSap": has_sap}]}
        },
    }


def test_candidate_query_confirmed_params() -> None:
    params = candidate_query()
    assert params["query.cond"] == "cancer"
    assert params["filter.overallStatus"] == "COMPLETED"
    assert params["filter.advanced"] == "AREA[StudyFirstPostDate]RANGE[2017-01-01,MAX]"
    assert params["aggFilters"] == "results:with"


def test_has_required_doc_true_when_protocol_present() -> None:
    study = _study("NCT00000001", has_protocol=True, has_sap=False)
    assert has_required_doc(study) is True


def test_has_required_doc_false_when_neither_present() -> None:
    study = _study("NCT00000001", has_protocol=False, has_sap=False)
    assert has_required_doc(study) is False


def test_extract_candidate_reads_confirmed_field_paths() -> None:
    study = _study(
        "NCT02872116",
        sponsor_name="Bristol-Myers Squibb",
        sponsor_class="INDUSTRY",
        phases=["PHASE3"],
    )
    candidate = extract_candidate(study)
    assert candidate == {
        "nct_id": "NCT02872116",
        "sponsor_name": "Bristol-Myers Squibb",
        "sponsor_class": "INDUSTRY",
        "phase": "PHASE3",
        "first_posted": "2018-01-01",
    }


def test_extract_candidate_handles_missing_phase() -> None:
    study = _study("NCT00000001", phases=[])
    candidate = extract_candidate(study)
    assert candidate["phase"] == "NA"


def _synthetic_pool(n: int, sponsors_per_class: int = 5) -> list[dict[str, Any]]:
    classes = ["INDUSTRY", "NIH", "OTHER"]
    phases = ["PHASE2", "PHASE3", "NA"]
    rng = random.Random(42)
    pool = []
    for i in range(n):
        sponsor_class = classes[i % len(classes)]
        phase = phases[(i // len(classes)) % len(phases)]
        sponsor_idx = rng.randrange(sponsors_per_class)
        pool.append(
            extract_candidate(
                _study(
                    f"NCT{i:08d}",
                    sponsor_name=f"{sponsor_class}-Sponsor-{sponsor_idx}",
                    sponsor_class=sponsor_class,
                    phases=[phase] if phase != "NA" else [],
                )
            )
        )
    return pool


def test_select_cohort_is_deterministic() -> None:
    pool = _synthetic_pool(400)
    first = select_cohort(pool, target=200)
    second = select_cohort(pool, target=200)
    assert first == second


def test_select_cohort_deterministic_under_input_reordering() -> None:
    pool = _synthetic_pool(400)
    shuffled = pool.copy()
    random.Random(7).shuffle(shuffled)
    assert select_cohort(pool, target=200) == select_cohort(shuffled, target=200)


def test_select_cohort_respects_sponsor_cap() -> None:
    pool = _synthetic_pool(400, sponsors_per_class=2)
    selected = select_cohort(pool, target=200, max_per_sponsor=3)
    counts: dict[str, int] = {}
    for c in selected:
        counts[c["sponsor_name"]] = counts.get(c["sponsor_name"], 0) + 1
    assert all(n <= 3 for n in counts.values())


def test_select_cohort_hits_target_when_pool_is_large_enough() -> None:
    pool = _synthetic_pool(400, sponsors_per_class=50)
    selected = select_cohort(pool, target=200)
    assert len(selected) == 200


def test_select_cohort_caps_at_pool_size() -> None:
    pool = _synthetic_pool(50, sponsors_per_class=50)
    selected = select_cohort(pool, target=200)
    assert len(selected) == 50


def test_select_cohort_dedupes_by_nct_id() -> None:
    pool = _synthetic_pool(10)
    doubled = pool + pool
    assert select_cohort(doubled, target=200) == select_cohort(pool, target=200)


def test_write_cohort_manifest_is_byte_identical_across_runs(tmp_path: Path) -> None:
    pool = _synthetic_pool(400)
    selected = select_cohort(pool, target=200)
    summary = stratification_summary(selected)

    out1 = tmp_path / "cohort_1.json"
    out2 = tmp_path / "cohort_2.json"
    write_cohort_manifest(selected, summary, out1)
    write_cohort_manifest(selected, summary, out2)

    assert out1.read_text() == out2.read_text()


def test_write_cohort_manifest_shape(tmp_path: Path) -> None:
    pool = _synthetic_pool(400)
    selected = select_cohort(pool, target=200)
    summary = stratification_summary(selected)
    out = tmp_path / "cohort.json"

    write_cohort_manifest(selected, summary, out)
    payload = json.loads(out.read_text())

    assert payload["condition"] == "oncology"
    assert payload["count"] == len(selected)
    assert len(payload["trials"]) == len(selected)
    assert payload["stratification_summary"] == summary


@pytest.mark.parametrize("target", [150, 200, 250])
def test_select_cohort_within_sprint_target_range_on_realistic_pool(target: int) -> None:
    # 3,132 candidates was the confirmed pool size in S0-04; a smaller
    # synthetic pool with many sponsors stands in for it here.
    pool = _synthetic_pool(1000, sponsors_per_class=200)
    selected = select_cohort(pool, target=target)
    assert 150 <= len(selected) <= 250


@responses.activate
def test_fetch_candidates_filters_to_doc_having_trials() -> None:
    with_doc = _study("NCT00000001", has_protocol=True, has_sap=False)
    without_doc = _study("NCT00000002", has_protocol=False, has_sap=False)
    responses.add(
        responses.GET,
        "https://clinicaltrials.gov/api/v2/studies",
        json={"studies": [with_doc, without_doc]},
    )

    client = RegistryClient(min_request_interval=0)
    candidates = fetch_candidates(client)

    assert [c["nct_id"] for c in candidates] == ["NCT00000001"]
