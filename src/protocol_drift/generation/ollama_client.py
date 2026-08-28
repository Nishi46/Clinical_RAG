"""Thin wrapper over Ollama's local REST API -- S3-06.

Plain `requests` calls against `http://localhost:11434` (project_plan.md
§11's "Plain Python" stack choice -- no new LLM client library). Verifies
the locally-resolved model digest matches the pinned one
(configs/models.yaml) before every call: an Ollama tag like
"llama3.1:latest" can be silently re-pulled to a new digest without this
project's knowledge otherwise, which is exactly the silent-drift failure
mode the appendix's "pin by digest, not tag" guarantee exists to catch.
"""

from __future__ import annotations

from typing import Any

import requests

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_S = 300


class DigestMismatchError(RuntimeError):
    """The locally-pulled model's digest no longer matches the pinned one."""


def _normalize_digest(digest: str) -> str:
    return digest.removeprefix("sha256:")


def resolved_digest(model_name: str, base_url: str = DEFAULT_BASE_URL) -> str:
    """The digest Ollama currently reports for this locally-pulled model
    tag, via `GET /api/tags` (the same data `ollama list` renders) --
    avoids shelling out to the `ollama` CLI and parsing its table output."""
    response = requests.get(f"{base_url}/api/tags", timeout=30)
    response.raise_for_status()
    for entry in response.json().get("models", []):
        if entry.get("name") == model_name:
            digest = entry.get("digest")
            if not digest:
                break
            return _normalize_digest(digest)
    raise DigestMismatchError(f"model {model_name!r} not found locally (ollama list)")


def verify_digest(model_name: str, expected_digest: str, base_url: str = DEFAULT_BASE_URL) -> None:
    actual = resolved_digest(model_name, base_url)
    expected = _normalize_digest(expected_digest)
    if actual != expected:
        raise DigestMismatchError(
            f"{model_name!r} digest drifted: pinned {expected}, locally resolved {actual} "
            "-- re-pin configs/models.yaml or pull the pinned digest explicitly"
        )


def generate(
    prompt: str,
    model: str,
    digest: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Verifies the pinned digest, then calls `/api/generate` (non-streamed)
    and returns Ollama's raw JSON response (callers read `response_text`
    plus eval_count/eval_duration etc. for cost logging)."""
    verify_digest(model, digest, base_url)
    response = requests.post(
        f"{base_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=timeout_s,
    )
    response.raise_for_status()
    return dict(response.json())
