"""Live network test against the real ClinicalTrials.gov API.

Excluded from the default CI run (pyproject.toml registers the
``integration`` marker; ``make test`` runs ``pytest -m "not integration"``).
Run manually: ``pytest -m integration``.
"""

import pytest

from protocol_drift.registry.client import RegistryClient


@pytest.mark.integration
def test_get_study_live_fetch() -> None:
    client = RegistryClient()
    study = client.get_study("NCT02872116")
    assert study["hasResults"] is True
