"""Dense retrieval -- S3-09, the vector leg of the hybrid ladder.

`dense_search` takes an already-computed query embedding (matching the
`vector_cosine_ops` HNSW index from S3-02); `embed_query` is the one place
that computes it, since query-side embedding needs a detail
document-chunk embedding (S3-01) doesn't: BAAI's bge-base-en-v1.5 model
card recommends prefixing *queries only* with an instruction string for
retrieval tasks -- passages are embedded as-is. Skipping this measurably
hurts recall; it's not optional polish.
"""

from __future__ import annotations

from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

QUERY_INSTRUCTION_PREFIX = "Represent this sentence for searching relevant passages: "

_DENSE_SEARCH_SQL = """
    SELECT chunk_id, embedding <=> %s AS distance
    FROM chunks
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> %s
    LIMIT %s
"""


def embed_query(query: str, embedder: Any) -> list[float]:
    vector = embedder.encode([QUERY_INSTRUCTION_PREFIX + query])[0]
    return list(vector)


def dense_search(
    query_embedding: list[float], k: int, conn: psycopg.Connection[Any]
) -> list[tuple[str, float]]:
    """Nearest neighbors by cosine distance -- returns (chunk_id, distance),
    ascending (closest first); a smaller distance is a better match, unlike
    lexical_search's descending ts_rank_cd score."""
    register_vector(conn)
    vector = Vector(query_embedding)
    with conn.cursor() as cur:
        cur.execute(_DENSE_SEARCH_SQL, (vector, vector, k))
        return [(row[0], row[1]) for row in cur.fetchall()]
