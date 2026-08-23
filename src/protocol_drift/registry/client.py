"""ClinicalTrials.gov API v2 client.

Endpoints and query params below are confirmed by direct probing in Sprint 0
(see scratch/field_paths.md and scratch/explore_api.py) rather than assumed
from documentation:

- ``GET /studies/{nct_id}`` and ``GET /studies`` (search) are the documented
  ``/api/v2`` surface. ``fields=`` (comma-separated) slims the payload;
  ``pageSize`` + ``pageToken``/``nextPageToken`` paginate.
- ``GET /api/int/studies/{nct_id}/history`` and
  ``GET /api/int/studies/{nct_id}/history/{version}`` are undocumented,
  unversioned endpoints that back the website's "History of Changes" tab.
  They are the only known source of amendment/version history. Treat them as
  unstable: re-verify before relying on them in Sprint 4 (S4-01).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://clinicaltrials.gov/api/v2"
DEFAULT_INTERNAL_BASE_URL = "https://clinicaltrials.gov/api/int"


class HistoryEndpointUnavailable(Exception):
    """Raised when the undocumented /api/int history endpoint returns a
    non-200 response or a response shape that doesn't match what Sprint 0
    confirmed. Callers should treat this as "history unknown", not crash."""


class RegistryClient:
    """Thin wrapper over the ClinicalTrials.gov API v2 (+ undocumented
    history endpoints) with pagination, retry/backoff, and polite rate
    limiting built in."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        internal_base_url: str = DEFAULT_INTERNAL_BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        min_request_interval: float = 0.5,
        max_retries: int = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._internal_base_url = internal_base_url.rstrip("/")
        self._session = session or requests.Session()
        self._timeout = timeout
        self._min_request_interval = min_request_interval
        self._max_retries = max_retries
        self._last_request_at: float | None = None

    # -- public API -----------------------------------------------------

    def get_study(self, nct_id: str, fields: list[str] | None = None) -> dict[str, Any]:
        """Fetch a single study's full (or field-restricted) current record."""
        params = {"fields": ",".join(fields)} if fields else None
        return self._get(f"{self._base_url}/studies/{nct_id}", params=params)

    def search_studies(self, page_size: int = 100, **query_params: Any) -> Iterator[dict[str, Any]]:
        """Yield individual study dicts, transparently following
        ``nextPageToken`` pagination. ``query_params`` are passed through as
        query string params verbatim (e.g. ``query.cond``, ``filter.overallStatus``,
        ``aggFilters``, ``fields``)."""
        params: dict[str, Any] = {"pageSize": page_size, **query_params}
        token: str | None = None
        while True:
            if token:
                params["pageToken"] = token
            data = self._get(f"{self._base_url}/studies", params=params)
            yield from data.get("studies", [])
            token = data.get("nextPageToken")
            if not token:
                return

    def get_history(self, nct_id: str) -> dict[str, Any]:
        """Full revision index: ``changes[]``, ``lastUpdateVersions{}``,
        ``outcomesUpdateCount``. Version 0 in ``changes[]`` is first-posted."""
        return self._get_internal(f"{self._internal_base_url}/studies/{nct_id}/history")

    def get_history_version(self, nct_id: str, version: int) -> dict[str, Any]:
        """Full point-in-time study snapshot at ``version``. Response shape:
        ``{"studyVersion": int, "study": {...same shape as get_study()...}}``."""
        return self._get_internal(f"{self._internal_base_url}/studies/{nct_id}/history/{version}")

    # -- internals --------------------------------------------------------

    def _get_internal(self, url: str) -> dict[str, Any]:
        try:
            return self._get(url)
        except requests.HTTPError as exc:
            raise HistoryEndpointUnavailable(f"{url} returned {exc}") from exc

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._respect_rate_limit()
        response = self._request_with_retry(url, params)
        result: dict[str, Any] = response.json()
        return result

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self._min_request_interval - elapsed
        if wait > 0:
            time.sleep(wait)

    def _request_with_retry(self, url: str, params: dict[str, Any] | None) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            self._last_request_at = time.monotonic()
            start = time.monotonic()
            try:
                response = self._session.get(url, params=params, timeout=self._timeout)
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.debug(
                    "GET %s -> %s (%.1fms)", response.url, response.status_code, elapsed_ms
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
