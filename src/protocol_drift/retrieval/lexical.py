"""Lexical retrieval -- S3-09, the BM25 leg of the hybrid ladder.

Postgres full-text search (`tsvector` + `ts_rank_cd`) is a cover-density
ranking function, not Okapi BM25 -- see retrieval/schema.sql's comment.
Every place this project's docs/tables say "BM25" it means "this lexical
leg," not a literal BM25 implementation.
"""

from __future__ import annotations

from typing import Any

import psycopg

from protocol_drift.retrieval.query_parse import QueryFilters, filters_to_where_clause


def lexical_search(
    query: str,
    k: int,
    conn: psycopg.Connection[Any],
    filters: QueryFilters | None = None,
) -> list[tuple[str, float]]:
    """`filters` (S3-10) narrows the candidate set to a specific
    trial/doc_type/doc_version before ranking -- same semantics as
    dense_search's `filters` param."""
    where_extra, extra_params = filters_to_where_clause(filters)
    sql = (
        "SELECT chunk_id, ts_rank_cd(text_search, query) AS score "
        "FROM chunks, plainto_tsquery('english', %s) query "
        f"WHERE text_search @@ query{where_extra} ORDER BY score DESC LIMIT %s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (query, *extra_params, k))
        return [(row[0], row[1]) for row in cur.fetchall()]
