import json
from pathlib import Path

import pytest

from protocol_drift.db.extract import (
    extract_amendments,
    extract_arms,
    extract_eligibility,
    extract_outcomes,
    extract_trial_row,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def current() -> dict:
    return json.loads((FIXTURES / "NCT02872116_current.json").read_text())


@pytest.fixture
def v0() -> dict:
    return json.loads((FIXTURES / "NCT02872116_v0.json").read_text())


@pytest.fixture
def history() -> dict:
    return json.loads((FIXTURES / "NCT02872116_history.json").read_text())


def test_extract_trial_row(current: dict) -> None:
    row = extract_trial_row(current)

    assert row["nct_id"] == "NCT02872116"
    assert row["sponsor_name"] == "Bristol-Myers Squibb"
    assert row["sponsor_class"] == "INDUSTRY"
    assert (
        row["condition"]
        == "Gastric Cancer|Gastroesophageal Junction Cancer|Esophageal Adenocarcinoma"
    )
    assert row["phase"] == "PHASE3"
    assert row["overall_status"] == "COMPLETED"
    # confirmed real fixture value -- exercises the has_protocol/has_sap
    # per-document logic (one doc flags protocol, a separate one flags SAP)
    assert row["has_protocol"] is True
    assert row["has_sap"] is True


def test_extract_outcomes_three_way_comparison(current: dict, v0: dict) -> None:
    outcomes = extract_outcomes(current, v0)

    by_source = {}
    for o in outcomes:
        by_source.setdefault(o["source"], []).append(o)

    assert set(by_source) == {"registered_current", "registered_first", "results_reported"}

    current_primary = [o for o in by_source["registered_current"] if o["kind"] == "PRIMARY"]
    assert len(current_primary) == 2
    assert current_primary[0]["measure"].startswith("Overall Survival (OS) in Participants Treated")
    assert current_primary[0]["version"] is None

    first_primary = [o for o in by_source["registered_first"] if o["kind"] == "PRIMARY"]
    assert len(first_primary) == 1
    assert first_primary[0]["measure"].startswith("Overall survival (OS) of nivolumab + ipilimumab")
    assert first_primary[0]["version"] == 0

    # Real, confirmed divergence between first-posted and current primary
    # outcome text -- exactly the pattern this project detects (S0-01 finding).
    assert current_primary[0]["measure"] != first_primary[0]["measure"]

    assert len(by_source["results_reported"]) == 9


def test_extract_outcomes_includes_secondary(current: dict, v0: dict) -> None:
    outcomes = extract_outcomes(current, v0)
    current_secondary = [
        o for o in outcomes if o["source"] == "registered_current" and o["kind"] == "SECONDARY"
    ]
    assert len(current_secondary) == 7


def test_extract_arms(current: dict) -> None:
    arms = extract_arms(current)

    assert len(arms) == 5
    labels = {a["arm_label"] for a in arms}
    assert "Nivolumab + Ipilimumab" in labels
    experimental = [a for a in arms if a["arm_type"] == "EXPERIMENTAL"]
    assert len(experimental) == 3


def test_extract_eligibility(current: dict) -> None:
    elig = extract_eligibility(current)

    assert elig is not None
    assert elig["nct_id"] == "NCT02872116"
    assert elig["sex"] == "ALL"
    assert elig["min_age"] == "18 Years"
    assert elig["max_age"] is None
    assert elig["criteria_text"] is not None and "Inclusion Criteria" in elig["criteria_text"]


def test_extract_amendments(history: dict) -> None:
    amendments = extract_amendments("NCT02872116", history)

    assert len(amendments) == 92
    assert amendments[0]["version"] == 0
    assert amendments[0]["modules_changed"] == []
    assert amendments[1]["version"] == 1
    assert "Study Status" in amendments[1]["modules_changed"]
