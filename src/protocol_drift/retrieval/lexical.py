"""Lexical retrieval -- S3-09, the BM25 leg of the hybrid ladder.

Postgres full-text search (`tsvector` + `ts_rank_cd`) is a cover-density
ranking function, not Okapi BM25 -- see retrieval/schema.sql's comment.
Every place this project's docs/tables say "BM25" it means "this lexical
leg," not a literal BM25 implementation.
"""

from __future__ import annotations

from typing import Any

import psycopg

_LEXICAL_SEARCH_SQL = """
    SELECT chunk_id, ts_rank_cd(text_search, query) AS score
    FROM chunks, plainto_tsquery('english', %s) query
    WHERE text_search @@ query
    ORDER BY score DESC
    LIMIT %s
"""


def lexical_search(query: str, k: int, conn: psycopg.Connection[Any]) -> list[tuple[str, float]]:
    with conn.cursor() as cur:
        cur.execute(_LEXICAL_SEARCH_SQL, (query, k))
        return [(row[0], row[1]) for row in cur.fetchall()]
