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


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    text: str
