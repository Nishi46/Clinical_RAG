"""PDF downloader — protocol/SAP documents for the frozen cohort.

CDN URL pattern confirmed by direct download in S0-01/S0-02 and re-confirmed
live here: ``https://cdn.clinicaltrials.gov/large-docs/{last 2 digits of the
numeric NCT ID}/{nct_id}/{filename}``. The CDN supports HEAD and returns
``Content-Length`` (confirmed live), which is what makes the resumability
check below possible without re-downloading a file just to check it.

doc_type precedence (protocol > sap > icf > other) matches the convention
already used in scratch/download_pdfs.py's S0-02 sample. A trial can have
more than one document of the same type (e.g. two "other" docs) — the
destination filename gets a numeric suffix in that case so a later one never
silently overwrites an earlier one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

CDN_BASE = "https://cdn.clinicaltrials.gov/large-docs"

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_SNAPSHOT_DIR = Path("data/registry_snapshots")
DEFAULT_PDF_DIR = Path("data/pdfs")


def _bucket(nct_id: str) -> str:
    numeric = "".join(ch for ch in nct_id if ch.isdigit())
    return numeric[-2:]


def _doc_type(doc: dict[str, Any]) -> str:
    if doc.get("hasProtocol"):
        return "protocol"
    if doc.get("hasSap"):
        return "sap"
    if doc.get("hasIcf"):
        return "icf"
    return "other"


def document_urls(nct_id: str, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a download ref per document on a trial's current record."""
    bucket = _bucket(nct_id)
    docs = snapshot.get("documentSection", {}).get("largeDocumentModule", {}).get("largeDocs", [])
    type_counts: dict[str, int] = {}
    refs = []
    for d in docs:
        doc_type = _doc_type(d)
        type_counts[doc_type] = type_counts.get(doc_type, 0) + 1
        suffix = "" if type_counts[doc_type] == 1 else f"_{type_counts[doc_type]}"
        filename = d["filename"]
        refs.append(
            {
                "nct_id": nct_id,
                "doc_type": doc_type,
                "filename": filename,
                "dest_name": f"{nct_id}_{doc_type}{suffix}.pdf",
                "url": f"{CDN_BASE}/{bucket}/{nct_id}/{filename}",
            }
        )
    return refs


class Downloader:
    """Streamed, resumable, polite PDF downloader with retry/backoff —
    mirrors RegistryClient's retry pattern but generalized to
    method+streaming for binary CDN downloads rather than JSON API calls."""

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        min_request_interval: float = 0.5,
        max_retries: int = 5,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout
        self._min_request_interval = min_request_interval
        self._max_retries = max_retries
        self._last_request_at: float | None = None

    def download_document(self, ref: dict[str, Any], dest_dir: Path) -> dict[str, Any]:
        dest_path = dest_dir / ref["nct_id"] / ref["dest_name"]
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        head = self._request(ref["url"], method="HEAD")
        expected_size = int(head.headers.get("Content-Length", -1))

        if dest_path.exists() and expected_size > 0 and dest_path.stat().st_size == expected_size:
            return {**ref, "status": "skipped", "local_path": str(dest_path), "size": expected_size}

        response = self._request(ref["url"], method="GET", stream=True)
        with dest_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1 << 16):
                f.write(chunk)
        size = dest_path.stat().st_size
        return {**ref, "status": "downloaded", "local_path": str(dest_path), "size": size}

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self._min_request_interval - elapsed
        if wait > 0:
            time.sleep(wait)

    def _request(self, url: str, method: str, **kwargs: Any) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            self._respect_rate_limit()
            self._last_request_at = time.monotonic()
            start = time.monotonic()
            try:
                response = self._session.request(method, url, timeout=self._timeout, **kwargs)
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.debug(
                    "%s %s -> %s (%.1fms)", method, response.url, response.status_code, elapsed_ms
                )
                response.raise_for_status()
                return response
            except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt == self._max_retries - 1:
                    break
                backoff = (2**attempt) + random.uniform(0, 1)
                logger.debug("request failed (%s), retrying in %.1fs", exc, backoff)
                time.sleep(backoff)
        assert last_exc is not None
        raise last_exc


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download_cohort(
    downloader: Downloader,
    cohort_path: Path = DEFAULT_COHORT_PATH,
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    dest_dir: Path = DEFAULT_PDF_DIR,
) -> None:
    cohort = json.loads(cohort_path.read_text())
    nct_ids = [t["nct_id"] for t in cohort["trials"]]

    manifest_entries = []
    error_lines = []
    for nct_id in nct_ids:
        snapshot_path = snapshot_dir / nct_id / "current.json"
        snapshot = json.loads(snapshot_path.read_text())
        for ref in document_urls(nct_id, snapshot):
            try:
                result = downloader.download_document(ref, dest_dir)
            except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as exc:
                error_lines.append(f"{nct_id}\t{ref['url']}\t{exc}")
                continue
            local_path = Path(result["local_path"])
            manifest_entries.append(
                {
                    "nct_id": nct_id,
                    "doc_type": result["doc_type"],
                    "filename": result["filename"],
                    "url": result["url"],
                    "local_path": result["local_path"],
                    "size": result["size"],
                    "sha256": _sha256_file(local_path),
                    # file mtime, not "now" -- a skipped (already-downloaded)
                    # file must keep its real download time, not be stamped
                    # with the time of this re-run's manifest rebuild.
                    "downloaded_at": datetime.fromtimestamp(
                        local_path.stat().st_mtime, tz=UTC
                    ).isoformat(),
                }
            )

    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "download_errors.log").write_text(
        "\n".join(error_lines) + ("\n" if error_lines else "")
    )
    manifest_entries.sort(key=lambda e: (e["nct_id"], e["doc_type"]))
    (dest_dir / "manifest.json").write_text(
        json.dumps({"entries": manifest_entries}, indent=2) + "\n"
    )

    print(f"{len(manifest_entries)} documents -> {dest_dir}")
    if error_lines:
        print(f"{len(error_lines)} download error(s) logged to {dest_dir / 'download_errors.log'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the frozen cohort's protocol/SAP PDFs.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--dest", type=Path, default=DEFAULT_PDF_DIR)
    args = parser.parse_args()

    downloader = Downloader()
    download_cohort(
        downloader, cohort_path=args.cohort, snapshot_dir=args.snapshot_dir, dest_dir=args.dest
    )


if __name__ == "__main__":
    main()
