"""Shared retrieval-result type -- used wherever a chunk's actual text (not
just its chunk_id) is needed after retrieval: answer generation (S3-06),
faithfulness scoring (S3-07), and cross-encoder reranking (S3-11).

Decoupled from `ingestion.models.Chunk` (which has no chunk_id field of its
own -- that's constructed externally) and from any specific retrieval
function's return type (dense_search/lexical_search/hybrid_search all
return bare chunk_id strings or (chunk_id, score) pairs; a caller that
needs the text fetches it separately and wraps it here).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    text: str


def fetch_chunks(conn: psycopg.Connection[Any], chunk_ids: list[str]) -> list[RetrievedChunk]:
    """Fetches text for a list of chunk_ids (typically a retrieve_fn's
    output) and wraps each as a RetrievedChunk, preserving the input
    order -- `= ANY(...)` makes no ordering guarantee of its own, and that
    order is the retrieval ranking callers need for prompts/reranking."""
    if not chunk_ids:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_id, text FROM chunks WHERE chunk_id = ANY(%s)", (chunk_ids,))
        text_by_id = dict(cur.fetchall())
    return [
        RetrievedChunk(chunk_id=chunk_id, text=text_by_id[chunk_id])
        for chunk_id in chunk_ids
        if chunk_id in text_by_id
    ]
