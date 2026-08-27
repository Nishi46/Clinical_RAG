"""Embedding pipeline -- S3-01.

Loads the pinned `sentence-transformers` model once (configs/models.yaml's
`embeddings` entry) and embeds every chunk in `data/chunks/`, caching each
result on disk keyed by `sha256(revision + chunk_id + text)` so a repeat run
-- or S3-02's later load into Postgres -- never re-encodes a chunk whose
text and pinned revision haven't changed. The chunk_id format
(`{nct_id}:{doc_type}:{chunk_index}`) matches what S3-02's `chunks` table
and S5-02's trace viewer both expect.

Reads only from data/chunks/ (S2-08/S2-09), never re-derives it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from protocol_drift.ingestion.models import Chunk

logger = logging.getLogger(__name__)

DEFAULT_CHUNKS_DIR = Path("data/chunks")
DEFAULT_EMBEDDINGS_DIR = Path("data/embeddings")
DEFAULT_ERROR_LOG = Path("data/embeddings_errors.log")
DEFAULT_BATCH_SIZE = 32

# Mirrors configs/models.yaml's `embeddings` entry -- kept as a literal here
# (no config-loading utility exists yet in this codebase) rather than
# parsing YAML for one pinned pair of values.
DEFAULT_MODEL_NAME = "BAAI/bge-base-en-v1.5"
DEFAULT_MODEL_REVISION = "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"


class EmbeddedChunk(BaseModel):
    """One cached embedding row. `embedding_cache_key` is the same value
    S3-02's `chunks.embedding_cache_key` column stores -- this is the file
    S3-02's loader reads to join embeddings onto chunks by `chunk_id`."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    embedding_cache_key: str
    embedding: list[float]


def chunk_id_for(chunk: Chunk, doc_label: str | None = None) -> str:
    """`{nct_id}:{doc_label}:{chunk_index}`. `doc_label` defaults to
    `chunk.doc_type`, but callers iterating `data/chunks/` must pass the
    source file's stem (e.g. "protocol_2") instead: S2-01's `_N` suffix
    convention means a handful of trials (e.g. NCT03083873) have more than
    one document of the same doc_type, each chunked with `chunk_index`
    restarting at 0 -- using bare `doc_type` for all of them collides two
    distinct chunks onto the same id."""
    label = doc_label if doc_label is not None else chunk.doc_type
    return f"{chunk.nct_id}:{label}:{chunk.chunk_index}"


def compute_cache_key(revision: str, chunk_id: str, text: str) -> str:
    return hashlib.sha256(f"{revision}{chunk_id}{text}".encode()).hexdigest()


def load_embedder(model_name: str, revision: str) -> Any:
    """Loads the sentence-transformers model once. Import is deferred to
    call time so nothing outside the `retrieval` optional dependency group
    needs `sentence-transformers` importable just to import this module."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, revision=revision)


def embed_chunks(
    chunks: Iterable[Chunk],
    embedder: Any,
    revision: str,
    cache: dict[str, EmbeddedChunk] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    doc_label: str | None = None,
) -> Iterator[EmbeddedChunk]:
    """Embeds `chunks` in batches of `batch_size`, reusing `cache` (keyed by
    `embedding_cache_key`) for any chunk already embedded under this
    revision. `embedder.encode` is only ever called with the subset of a
    batch that misses the cache -- a batch that's entirely cache hits never
    calls it at all, which is what makes a fully-cached re-run a no-op.
    `doc_label` is forwarded to `chunk_id_for` -- see its docstring."""
    cache = cache if cache is not None else {}
    batch: list[Chunk] = []

    def flush(batch: list[Chunk]) -> Iterator[EmbeddedChunk]:
        keyed = [(c, chunk_id_for(c, doc_label)) for c in batch]
        keyed_with_cache_key = [
            (c, cid, compute_cache_key(revision, cid, c.text)) for c, cid in keyed
        ]
        misses = [(c, cid, key) for c, cid, key in keyed_with_cache_key if key not in cache]
        if misses:
            vectors = embedder.encode([c.text for c, _, _ in misses])
            for (_, cid, key), vector in zip(misses, vectors, strict=True):
                cache[key] = EmbeddedChunk(
                    chunk_id=cid, embedding_cache_key=key, embedding=list(vector)
                )
        for _, _, key in keyed_with_cache_key:
            yield cache[key]

    for chunk in chunks:
        batch.append(chunk)
        if len(batch) >= batch_size:
            yield from flush(batch)
            batch = []
    if batch:
        yield from flush(batch)


def _load_cache(path: Path) -> dict[str, EmbeddedChunk]:
    if not path.exists():
        return {}
    cache: dict[str, EmbeddedChunk] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        embedded = EmbeddedChunk.model_validate_json(line)
        cache[embedded.embedding_cache_key] = embedded
    return cache


def _write_cache(path: Path, embedded_chunks: list[EmbeddedChunk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for embedded in embedded_chunks:
            f.write(embedded.model_dump_json() + "\n")


def _read_chunks(path: Path, errors: list[str]) -> list[Chunk]:
    chunks = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            chunk = Chunk(**raw)
        except Exception as exc:
            errors.append(f"{path}\tline {line_number}\t{exc}")
            continue
        if not chunk.text.strip():
            errors.append(f"{path}\tline {line_number}\tempty text")
            continue
        chunks.append(chunk)
    return chunks


@dataclass
class EmbeddingReport:
    documents: int
    chunks: int
    cache_hits: int
    newly_embedded: int
    failed: int


def embed_corpus(
    chunks_dir: Path = DEFAULT_CHUNKS_DIR,
    embeddings_dir: Path = DEFAULT_EMBEDDINGS_DIR,
    error_log_path: Path = DEFAULT_ERROR_LOG,
    model_name: str = DEFAULT_MODEL_NAME,
    revision: str = DEFAULT_MODEL_REVISION,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force: bool = False,
) -> EmbeddingReport:
    embedder = load_embedder(model_name, revision)

    documents = 0
    total_chunks = 0
    cache_hits = 0
    newly_embedded = 0
    errors: list[str] = []

    for chunks_path in sorted(chunks_dir.glob("*/*.jsonl")):
        nct_id = chunks_path.parent.name
        dest_path = embeddings_dir / nct_id / chunks_path.name

        chunks = _read_chunks(chunks_path, errors)
        if not chunks:
            continue

        existing_cache = {} if force else _load_cache(dest_path)
        cache_before = set(existing_cache)

        try:
            embedded = list(
                embed_chunks(
                    chunks,
                    embedder,
                    revision,
                    cache=existing_cache,
                    batch_size=batch_size,
                    doc_label=chunks_path.stem,
                )
            )
        except Exception as exc:
            errors.append(f"{chunks_path}\tencode error\t{exc}")
            continue

        _write_cache(dest_path, embedded)

        documents += 1
        total_chunks += len(embedded)
        hits = sum(1 for e in embedded if e.embedding_cache_key in cache_before)
        cache_hits += hits
        newly_embedded += len(embedded) - hits

    if errors:
        error_log_path.parent.mkdir(parents=True, exist_ok=True)
        error_log_path.write_text("\n".join(errors) + "\n")

    report = EmbeddingReport(
        documents=documents,
        chunks=total_chunks,
        cache_hits=cache_hits,
        newly_embedded=newly_embedded,
        failed=len(errors),
    )
    print(
        f"Embedded {report.documents} document(s) -> {report.chunks} chunks "
        f"({report.cache_hits} cache hits, {report.newly_embedded} newly embedded, "
        f"{report.failed} failed) -> {embeddings_dir}"
    )
    if errors:
        print(f"{len(errors)} error(s) -- see {error_log_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed the section-aware chunk corpus.")
    parser.add_argument("--chunks-dir", type=Path, default=DEFAULT_CHUNKS_DIR)
    parser.add_argument("--embeddings-dir", type=Path, default=DEFAULT_EMBEDDINGS_DIR)
    parser.add_argument("--error-log", type=Path, default=DEFAULT_ERROR_LOG)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    embed_corpus(
        chunks_dir=args.chunks_dir,
        embeddings_dir=args.embeddings_dir,
        error_log_path=args.error_log,
        model_name=args.model_name,
        revision=args.revision,
        batch_size=args.batch_size,
        force=args.force,
    )


if __name__ == "__main__":
    main()
