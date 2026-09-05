"""Answer generation -- S3-06.

Builds a numbered-excerpt prompt from retrieved chunks (each excerpt's text
already carries its S2-08 contextual header as its first line, so no extra
header-building is needed here), calls Ollama, and logs every call through
the trace store -- caching on (model_digest, prompt_hash) so a re-run over
an unchanged prompt never calls Ollama a second time.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from protocol_drift.eval.models import EvalQuestion
from protocol_drift.generation import ollama_client
from protocol_drift.retrieval.types import RetrievedChunk
from protocol_drift.trace.store import TraceStore, compute_prompt_hash

# Mirrors configs/models.yaml's `generation` entry -- kept as a literal here
# (no config-loading utility exists yet in this codebase), same convention
# as S3-01/S3-03's DEFAULT_MODEL_NAME/DEFAULT_MODEL_REVISION constants.
DEFAULT_MODEL_NAME = "llama3.1:latest"
DEFAULT_MODEL_DIGEST = "sha256:46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e"

REFUSAL_TOKEN = "NOT_ANSWERABLE"

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")

__all__ = [
    "RetrievedChunk",  # re-exported: this module's original home before S3-11
    "GeneratedAnswer",
    "build_prompt",
    "cached_generate",
    "generate_answer",
    "stream_answer",
    "is_refusal",
]


def is_refusal(response_text: str) -> bool:
    """Exact match against `REFUSAL_TOKEN` only -- a hedge like "I'm not
    sure, but possibly X" is not a clean refusal (S4-09's adversarial/
    refusal-metrics eval and this function share this exact check, so a
    generated answer counts as refusing the same way everywhere it's
    checked)."""
    return response_text.strip() == REFUSAL_TOKEN


@dataclass
class GeneratedAnswer:
    query_id: int
    generation_id: int
    response_text: str
    cited_chunk_ids: list[str]
    is_refusal: bool
    from_cache: bool


def build_prompt(question: EvalQuestion, chunks: Sequence[RetrievedChunk]) -> str:
    excerpts = "\n\n".join(f"[{i}]\n{chunk.text}" for i, chunk in enumerate(chunks, start=1))
    return (
        "Answer the question using only the excerpts below. Each excerpt is numbered "
        "and begins with a contextual header identifying its trial, document type, "
        "and section.\n\n"
        f"{excerpts}\n\n"
        f"Question: {question.question_text}\n\n"
        "Cite the excerpt number(s) that support your answer inline using bracket "
        "notation, e.g. [1] or [2][3]. If the excerpts do not contain the answer, "
        f"respond with exactly: {REFUSAL_TOKEN}"
    )


def _parse_citations(response_text: str, chunks: Sequence[RetrievedChunk]) -> list[str]:
    cited_chunk_ids: list[str] = []
    seen: set[int] = set()
    for match in _CITATION_PATTERN.finditer(response_text):
        index = int(match.group(1))
        if 1 <= index <= len(chunks) and index not in seen:
            seen.add(index)
            cited_chunk_ids.append(chunks[index - 1].chunk_id)
    return cited_chunk_ids


def cached_generate(
    prompt: str,
    query_id: int,
    store: TraceStore,
    model: str,
    digest: str,
) -> tuple[str, int, bool]:
    """One cached + traced model call, generic over any prompt (an answer
    prompt, a judge-scoring prompt, a claim-extraction prompt, ...):
    computes `prompt_hash` and reuses a prior response if `store` already
    has a generation row for this exact (model_digest, prompt_hash) pair,
    otherwise calls Ollama for real. Always logs a fresh generation + cost
    row under `query_id` -- a cache hit still gets traced, it just costs
    zero new tokens/wall-clock time, since the original row already
    carries the real cost of producing this response.

    Returns (response_text, generation_id, from_cache)."""
    prompt_hash = compute_prompt_hash(digest, prompt)
    cached = store.find_generation(digest, prompt_hash)
    from_cache = cached is not None
    if cached is not None:
        response_text = cached["response_text"]
        token_count = cached["token_count"]
        latency_ms = 0.0
        tokens_in = cached["tokens_in"] or 0
        tokens_out = cached["tokens_out"] or 0
        wall_clock_ms = 0.0
    else:
        start = time.monotonic()
        result = ollama_client.generate(prompt, model, digest)
        latency_ms = (time.monotonic() - start) * 1000
        response_text = result["response"]
        tokens_in = result.get("prompt_eval_count", 0)
        tokens_out = result.get("eval_count", 0)
        token_count = tokens_in + tokens_out
        wall_clock_ms = result.get("total_duration", 0) / 1e6  # ns -> ms

    generation_id = store.log_generation(
        query_id, digest, prompt_hash, response_text, latency_ms, token_count
    )
    store.log_cost(generation_id, tokens_in, tokens_out, wall_clock_ms)
    return response_text, generation_id, from_cache


def generate_answer(
    question: EvalQuestion,
    retrieved_chunks: Sequence[RetrievedChunk],
    store: TraceStore,
    model: str = DEFAULT_MODEL_NAME,
    digest: str = DEFAULT_MODEL_DIGEST,
    tier: str | None = None,
    query_id: int | None = None,
) -> GeneratedAnswer:
    """`query_id`, if given, reuses an already-logged query (e.g. S3-12's
    `run_rung`, which needs retrieval and generation for the same question
    to share one query row so its per-question trace reads as a single
    coherent pipeline) instead of logging a new one."""
    prompt = build_prompt(question, retrieved_chunks)
    if query_id is None:
        query_id = store.log_query(question.question_text, tier=tier)
    response_text, generation_id, from_cache = cached_generate(
        prompt, query_id, store, model, digest
    )

    refused = is_refusal(response_text)
    cited_chunk_ids = [] if refused else _parse_citations(response_text, retrieved_chunks)

    return GeneratedAnswer(
        query_id=query_id,
        generation_id=generation_id,
        response_text=response_text,
        cited_chunk_ids=cited_chunk_ids,
        is_refusal=refused,
        from_cache=from_cache,
    )


def stream_answer(
    question: EvalQuestion,
    retrieved_chunks: Sequence[RetrievedChunk],
    store: TraceStore,
    model: str = DEFAULT_MODEL_NAME,
    digest: str = DEFAULT_MODEL_DIGEST,
    tier: str | None = None,
    query_id: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Streaming sibling of `generate_answer` -- same prompt-building,
    caching, and trace-logging contract (shares `build_prompt`,
    `compute_prompt_hash`, `is_refusal`, `_parse_citations` with it, rather
    than reimplementing them), but yields the response as it arrives from
    Ollama instead of returning it as one block, for S5-01's SSE endpoint to
    forward live. Yields `{"type": "token", "text": ...}` per piece of text,
    then exactly one final `{"type": "done", ...}` carrying the same fields
    `GeneratedAnswer` does. A cache hit still logs a fresh generation/cost
    row under this `query_id` (every call stays traced, per `cached_generate`'s
    contract) but has nothing to stream live, so its full cached text is
    yielded as a single token event."""
    prompt = build_prompt(question, retrieved_chunks)
    if query_id is None:
        query_id = store.log_query(question.question_text, tier=tier)

    prompt_hash = compute_prompt_hash(digest, prompt)
    cached = store.find_generation(digest, prompt_hash)

    if cached is not None:
        response_text = cached["response_text"]
        yield {"type": "token", "text": response_text}
        token_count = cached["token_count"]
        tokens_in = cached["tokens_in"] or 0
        tokens_out = cached["tokens_out"] or 0
        latency_ms = 0.0
        wall_clock_ms = 0.0
        from_cache = True
    else:
        pieces: list[str] = []
        tokens_in = tokens_out = 0
        wall_clock_ms = 0.0
        start = time.monotonic()
        for event in ollama_client.generate_stream(prompt, model, digest):
            piece = event.get("response", "")
            if piece:
                pieces.append(piece)
                yield {"type": "token", "text": piece}
            if event.get("done"):
                tokens_in = event.get("prompt_eval_count", 0)
                tokens_out = event.get("eval_count", 0)
                wall_clock_ms = event.get("total_duration", 0) / 1e6  # ns -> ms
        latency_ms = (time.monotonic() - start) * 1000
        response_text = "".join(pieces)
        token_count = tokens_in + tokens_out
        from_cache = False

    generation_id = store.log_generation(
        query_id, digest, prompt_hash, response_text, latency_ms, token_count
    )
    store.log_cost(generation_id, tokens_in, tokens_out, wall_clock_ms)

    refused = is_refusal(response_text)
    cited_chunk_ids = [] if refused else _parse_citations(response_text, retrieved_chunks)

    yield {
        "type": "done",
        "query_id": query_id,
        "generation_id": generation_id,
        "cited_chunk_ids": cited_chunk_ids,
        "is_refusal": refused,
        "from_cache": from_cache,
    }
