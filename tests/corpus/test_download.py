import json
from pathlib import Path
from typing import Any

import pytest
import responses

from protocol_drift.corpus.download import Downloader, document_urls, download_cohort


def _snapshot(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"documentSection": {"largeDocumentModule": {"largeDocs": docs}}}


@pytest.fixture
def downloader() -> Downloader:
    return Downloader(min_request_interval=0)


def test_document_urls_confirmed_cdn_pattern() -> None:
    snapshot = _snapshot([{"filename": "Prot_000.pdf", "hasProtocol": True, "hasSap": False}])
    refs = document_urls("NCT02798211", snapshot)

    assert len(refs) == 1
    assert refs[0]["url"] == "https://cdn.clinicaltrials.gov/large-docs/11/NCT02798211/Prot_000.pdf"
    assert refs[0]["dest_name"] == "NCT02798211_protocol.pdf"


@pytest.mark.parametrize(
    ("flags", "expected_type"),
    [
        ({"hasProtocol": True, "hasSap": True}, "protocol"),  # protocol takes precedence
        ({"hasSap": True}, "sap"),
        ({"hasIcf": True}, "icf"),
        ({}, "other"),
    ],
)
def test_document_urls_doc_type_precedence(flags: dict[str, bool], expected_type: str) -> None:
    snapshot = _snapshot([{"filename": "doc.pdf", **flags}])
    refs = document_urls("NCT00000001", snapshot)
    assert refs[0]["doc_type"] == expected_type


def test_document_urls_dedupes_same_type_filenames() -> None:
    snapshot = _snapshot(
        [
            {"filename": "a.pdf"},  # both classify as "other"
            {"filename": "b.pdf"},
        ]
    )
    refs = document_urls("NCT00000001", snapshot)
    dest_names = [r["dest_name"] for r in refs]
    assert dest_names == ["NCT00000001_other.pdf", "NCT00000001_other_2.pdf"]


@responses.activate
def test_downloader_downloads_new_file(downloader: Downloader, tmp_path: Path) -> None:
    body = b"%PDF-1.4 fake content"
    responses.add(
        responses.HEAD,
        "https://cdn.clinicaltrials.gov/large-docs/11/NCT02798211/Prot_000.pdf",
        headers={"Content-Length": str(len(body))},
    )
    responses.add(
        responses.GET,
        "https://cdn.clinicaltrials.gov/large-docs/11/NCT02798211/Prot_000.pdf",
        body=body,
    )
    ref = document_urls(
        "NCT02798211", _snapshot([{"filename": "Prot_000.pdf", "hasProtocol": True}])
    )[0]

    result = downloader.download_document(ref, tmp_path)

    assert result["status"] == "downloaded"
    assert Path(result["local_path"]).read_bytes() == body
    assert result["size"] == len(body)


@responses.activate
def test_downloader_skips_when_size_matches(downloader: Downloader, tmp_path: Path) -> None:
    body = b"%PDF-1.4 fake content"
    dest = tmp_path / "NCT02798211" / "NCT02798211_protocol.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(body)

    responses.add(
        responses.HEAD,
        "https://cdn.clinicaltrials.gov/large-docs/11/NCT02798211/Prot_000.pdf",
        headers={"Content-Length": str(len(body))},
    )
    ref = document_urls(
        "NCT02798211", _snapshot([{"filename": "Prot_000.pdf", "hasProtocol": True}])
    )[0]

    result = downloader.download_document(ref, tmp_path)

    assert result["status"] == "skipped"
    # only the HEAD request was registered/consumed -- no GET call was made
    assert len(responses.calls) == 1


@responses.activate
def test_downloader_redownloads_on_size_mismatch(downloader: Downloader, tmp_path: Path) -> None:
    stale_body = b"stale partial content"
    fresh_body = b"%PDF-1.4 the real, complete file"
    dest = tmp_path / "NCT02798211" / "NCT02798211_protocol.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(stale_body)

    responses.add(
        responses.HEAD,
        "https://cdn.clinicaltrials.gov/large-docs/11/NCT02798211/Prot_000.pdf",
        headers={"Content-Length": str(len(fresh_body))},
    )
    responses.add(
        responses.GET,
        "https://cdn.clinicaltrials.gov/large-docs/11/NCT02798211/Prot_000.pdf",
        body=fresh_body,
    )
    ref = document_urls(
        "NCT02798211", _snapshot([{"filename": "Prot_000.pdf", "hasProtocol": True}])
    )[0]

    result = downloader.download_document(ref, tmp_path)

    assert result["status"] == "downloaded"
    assert dest.read_bytes() == fresh_body


@responses.activate
def test_download_cohort_writes_manifest_and_error_log(
    downloader: Downloader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the 404 case below exhausts all retry attempts; skip the real backoff sleeps
    monkeypatch.setattr("protocol_drift.corpus.download.time.sleep", lambda _seconds: None)

    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(
        json.dumps({"trials": [{"nct_id": "NCT00000001"}, {"nct_id": "NCT00000002"}]})
    )

    snapshot_dir = tmp_path / "snapshots"
    for nct_id, filename in [("NCT00000001", "Prot_000.pdf"), ("NCT00000002", "Prot_001.pdf")]:
        trial_dir = snapshot_dir / nct_id
        trial_dir.mkdir(parents=True)
        (trial_dir / "current.json").write_text(
            json.dumps(_snapshot([{"filename": filename, "hasProtocol": True}]))
        )

    body = b"%PDF-1.4 ok"
    responses.add(
        responses.HEAD,
        "https://cdn.clinicaltrials.gov/large-docs/01/NCT00000001/Prot_000.pdf",
        headers={"Content-Length": str(len(body))},
    )
    responses.add(
        responses.GET,
        "https://cdn.clinicaltrials.gov/large-docs/01/NCT00000001/Prot_000.pdf",
        body=body,
    )
    # NCT00000002's document 404s -- must be logged, not raised
    responses.add(
        responses.HEAD,
        "https://cdn.clinicaltrials.gov/large-docs/02/NCT00000002/Prot_001.pdf",
        status=404,
    )

    dest_dir = tmp_path / "pdfs"
    download_cohort(
        downloader, cohort_path=cohort_path, snapshot_dir=snapshot_dir, dest_dir=dest_dir
    )

    manifest = json.loads((dest_dir / "manifest.json").read_text())
    assert len(manifest["entries"]) == 1
    assert manifest["entries"][0]["nct_id"] == "NCT00000001"
    assert len(manifest["entries"][0]["sha256"]) == 64

    errors = (dest_dir / "download_errors.log").read_text()
    assert "NCT00000002" in errors
