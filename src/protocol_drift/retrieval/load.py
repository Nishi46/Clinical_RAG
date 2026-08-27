"""Loads S2-08/S2-09's chunk corpus and S3-01's cached embeddings into the
`chunks` table -- the join point between the two file-based pipelines and
the `chunks` table dense/lexical/metadata indexes retrieval reads from.

Reads only data/chunks/ and data/embeddings/, never re-derives either.
Idempotent: `ON CONFLICT (chunk_id) DO UPDATE` so a re-run after a chunker
or embedding fix updates rows in place instead of duplicating them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from protocol_drift.db import DEFAULT_DSN
from protocol_drift.ingestion.models import Chunk
from protocol_drift.retrieval.embed import chunk_id_for

DEFAULT_CHUNKS_DIR = Path("data/chunks")
DEFAULT_EMBEDDINGS_DIR = Path("data/embeddings")

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_UPSERT_CHUNK = """
    INSERT INTO chunks (
        chunk_id, nct_id, doc_type, doc_version, section, subsection,
        page_range, chunk_type, is_ocr, text, embedding, embedding_cache_key
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (chunk_id) DO UPDATE SET
        nct_id = EXCLUDED.nct_id,
        doc_type = EXCLUDED.doc_type,
        doc_version = EXCLUDED.doc_version,
        section = EXCLUDED.section,
        subsection = EXCLUDED.subsection,
        page_range = EXCLUDED.page_range,
        chunk_type = EXCLUDED.chunk_type,
        is_ocr = EXCLUDED.is_ocr,
        text = EXCLUDED.text,
        embedding = EXCLUDED.embedding,
        embedding_cache_key = EXCLUDED.embedding_cache_key
"""


def _load_embeddings(path: Path) -> dict[str, tuple[str, list[float]]]:
    """chunk_id -> (embedding_cache_key, embedding), from S3-01's cache file
    for this (nct_id, doc_type). Empty dict if S3-01 never ran for it (a
    document with zero chunks, per embed_corpus's own skip-if-empty rule)."""
    if not path.exists():
        return {}
    embeddings = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        embeddings[row["chunk_id"]] = (row["embedding_cache_key"], row["embedding"])
    return embeddings


def _chunk_row(
    chunk: Chunk, chunk_id: str, embedding_row: tuple[str, list[float]] | None
) -> tuple[Any, ...]:
    cache_key, embedding = embedding_row if embedding_row is not None else (None, None)
    return (
        chunk_id,
        chunk.nct_id,
        chunk.doc_type,
        chunk.doc_version,
        chunk.section,
        chunk.subsection,
        f"{chunk.page_range[0]}-{chunk.page_range[1]}",
        chunk.chunk_type,
        chunk.is_ocr,
        chunk.text,
        Vector(embedding) if embedding is not None else None,
        cache_key,
    )


def _known_nct_ids(conn: psycopg.Connection[Any]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT nct_id FROM trials")
        return {row[0] for row in cur.fetchall()}


def load_chunks_into_db(
    chunks_dir: Path,
    embeddings_dir: Path,
    conn: psycopg.Connection[Any],
) -> dict[str, int]:
    register_vector(conn)
    known_nct_ids = _known_nct_ids(conn)

    loaded = 0
    missing_embeddings: list[str] = []
    skipped_non_cohort: dict[str, int] = {}

    with conn.cursor() as cur:
        for chunks_path in sorted(chunks_dir.glob("*/*.jsonl")):
            nct_id = chunks_path.parent.name
            if nct_id not in known_nct_ids:
                # chunks.nct_id REFERENCES trials(nct_id) -- a document that
                # was chunked (e.g. a spot-check/reference fixture like
                # NCT02872116, used throughout docs/ingestion.md but never
                # part of the actual sampled cohort) has no trials row and
                # would violate that FK. Skip and report rather than
                # fabricating a registry row for a trial that was never
                # downloaded or sampled.
                n = sum(1 for line in chunks_path.read_text().splitlines() if line.strip())
                skipped_non_cohort[nct_id] = skipped_non_cohort.get(nct_id, 0) + n
                continue

            embeddings = _load_embeddings(embeddings_dir / nct_id / chunks_path.name)
            doc_label = chunks_path.stem  # matches S3-01's embed_corpus doc_label exactly

            rows = []
            for line in chunks_path.read_text().splitlines():
                if not line.strip():
                    continue
                chunk = Chunk(**json.loads(line))
                chunk_id = chunk_id_for(chunk, doc_label)
                embedding_row = embeddings.get(chunk_id)
                if embedding_row is None:
                    missing_embeddings.append(chunk_id)
                rows.append(_chunk_row(chunk, chunk_id, embedding_row))

            if rows:
                cur.executemany(_UPSERT_CHUNK, rows)
                loaded += len(rows)

    conn.commit()

    if missing_embeddings:
        print(
            f"WARNING: {len(missing_embeddings)} chunk(s) loaded with no matching "
            f"embedding -- run protocol_drift.retrieval.embed first. First few: "
            f"{missing_embeddings[:5]}"
        )
    if skipped_non_cohort:
        total_skipped = sum(skipped_non_cohort.values())
        print(
            f"WARNING: skipped {total_skipped} chunk(s) from {len(skipped_non_cohort)} "
            f"document(s) not in the trials table (not part of the sampled cohort): "
            f"{skipped_non_cohort}"
        )
    return {
        "loaded": loaded,
        "missing_embeddings": len(missing_embeddings),
        "skipped_non_cohort": sum(skipped_non_cohort.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load the chunk corpus + cached embeddings into Postgres."
    )
    parser.add_argument("--chunks-dir", type=Path, default=DEFAULT_CHUNKS_DIR)
    parser.add_argument("--embeddings-dir", type=Path, default=DEFAULT_EMBEDDINGS_DIR)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument(
        "--apply-schema",
        action="store_true",
        help="run retrieval/schema.sql before loading (safe to repeat: CREATE ... IF NOT EXISTS)",
    )
    args = parser.parse_args()

    conn = psycopg.connect(args.dsn)
    if args.apply_schema:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text())
        conn.commit()

    result = load_chunks_into_db(args.chunks_dir, args.embeddings_dir, conn)
    print(f"Loaded {result['loaded']} chunk(s) into {args.dsn}")


if __name__ == "__main__":
    main()
