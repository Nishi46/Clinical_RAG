from typing import Any

import pytest
import responses

from protocol_drift.registry.client import HistoryEndpointUnavailable, RegistryClient


@pytest.fixture
def client() -> RegistryClient:
    # min_request_interval=0 keeps the mocked-HTTP tests fast; live client
    # usage should pass the default (0.5s) or higher.
    return RegistryClient(min_request_interval=0)


@responses.activate
def test_search_studies_paginates_without_duplicates(client: RegistryClient) -> None:
    page1 = {
        "studies": [
            {"protocolSection": {"identificationModule": {"nctId": f"NCT{i:08d}"}}}
            for i in range(3)
        ],
        "nextPageToken": "page2token",
    }
    page2 = {
        "studies": [
            {"protocolSection": {"identificationModule": {"nctId": f"NCT{i:08d}"}}}
            for i in range(3, 5)
        ],
    }
    responses.add(
        responses.GET,
        "https://clinicaltrials.gov/api/v2/studies",
        json=page1,
        match=[responses.matchers.query_param_matcher({"pageSize": "100"}, strict_match=False)],
    )
    responses.add(
        responses.GET,
        "https://clinicaltrials.gov/api/v2/studies",
        json=page2,
        match=[
            responses.matchers.query_param_matcher({"pageToken": "page2token"}, strict_match=False)
        ],
    )

    studies = list(client.search_studies())
    nct_ids = [s["protocolSection"]["identificationModule"]["nctId"] for s in studies]

    assert len(nct_ids) == 5
    assert len(set(nct_ids)) == 5  # no duplicates across the two pages


@responses.activate
def test_fields_param_passed_through(client: RegistryClient) -> None:
    responses.add(
        responses.GET,
        "https://clinicaltrials.gov/api/v2/studies/NCT02872116",
        json={"protocolSection": {}},
    )

    client.get_study("NCT02872116", fields=["NCTId", "BriefTitle"])

    assert len(responses.calls) == 1
    request_url = responses.calls[0].request.url
    assert request_url is not None
    assert "fields=NCTId%2CBriefTitle" in request_url


@responses.activate
def test_retries_on_429_then_succeeds(
    client: RegistryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("protocol_drift.registry.client.time.sleep", lambda _seconds: None)

    responses.add(
        responses.GET, "https://clinicaltrials.gov/api/v2/studies/NCT02872116", status=429
    )
    responses.add(
        responses.GET,
        "https://clinicaltrials.gov/api/v2/studies/NCT02872116",
        json={"protocolSection": {"identificationModule": {"nctId": "NCT02872116"}}},
        status=200,
    )

    result = client.get_study("NCT02872116")

    assert result["protocolSection"]["identificationModule"]["nctId"] == "NCT02872116"
    assert len(responses.calls) == 2


@responses.activate
def test_get_history_raises_on_error_status(
    client: RegistryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("protocol_drift.registry.client.time.sleep", lambda _seconds: None)

    responses.add(
        responses.GET,
        "https://clinicaltrials.gov/api/int/studies/NCT02872116/history",
        status=500,
    )

    with pytest.raises(HistoryEndpointUnavailable):
        client.get_history("NCT02872116")


@responses.activate
def test_get_history_version_returns_snapshot(client: RegistryClient) -> None:
    payload: dict[str, Any] = {
        "studyVersion": 0,
        "study": {"protocolSection": {"identificationModule": {"nctId": "NCT02872116"}}},
    }
    responses.add(
        responses.GET,
        "https://clinicaltrials.gov/api/int/studies/NCT02872116/history/0",
        json=payload,
    )

    result = client.get_history_version("NCT02872116", 0)

    assert result["studyVersion"] == 0
    assert result["study"]["protocolSection"]["identificationModule"]["nctId"] == "NCT02872116"
