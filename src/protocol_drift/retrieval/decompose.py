"""Cross-source query decomposition -- S4-04. Ladder rung 6, depends on
S3-11's `rerank_ladder`.

For a T3-style cross-source comparison question, decomposition doesn't
need a heavy NLP split: two of the three legs (`registered_first`,
`registered_current`) are already structured Postgres `outcomes` rows, not
free text to retrieve -- the only leg that needs real retrieval is the
protocol-side endpoint statement, which S2-04's section taxonomy already
labels `objectives`. So `decompose_cross_source_query` mostly documents
"don't try to retrieve what's already structured" rather than doing
NLP-style splitting; the real work is in `answer_cross_source_query`,
which runs each leg for real and returns raw text (or an explicit `None`)
plus provenance -- the direct input to S4-05's three independent pairwise
verdicts, not itself a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import psycopg

from protocol_drift.retrieval.query_parse import QueryFilters
from protocol_drift.retrieval.rerank import DEFAULT_TOP_K, rerank_ladder
from protocol_drift.retrieval.types import fetch_chunks
from protocol_drift.trace.store import TraceStore, traced_call

Leg = Literal["protocol", "registered_first", "registered_current"]

# S1-05's outcomes.source values for the two registry-side legs (the third,
# 'results_reported', is S4-05's job -- only relevant when hasResults, per
# discrepancy_definition.md SS2 -- not this module's concern).
_REGISTRY_LEGS: tuple[Leg, ...] = ("registered_first", "registered_current")


@dataclass(frozen=True)
class SubQuery:
    leg: Leg
    query_text: str | None  # None for the registry legs -- a direct lookup, not a retrieval query
    filters: QueryFilters | None


def decompose_cross_source_query(query: str, nct_id: str) -> list[SubQuery]:
    """One protocol-side retrieval sub-query (prefiltered to this trial's
    `objectives` section) and two registry-side structured lookups that
    carry no retrieval query text at all."""
    return [
        SubQuery(
            leg="protocol",
            query_text=query,
            filters=QueryFilters(nct_id=nct_id, doc_type="protocol", section="objectives"),
        ),
        *(SubQuery(leg=leg, query_text=None, filters=None) for leg in _REGISTRY_LEGS),
    ]


@dataclass
class CrossSourceAnswer:
    protocol_leg: str | None
    protocol_chunk_id: str | None
    registered_first: str | None
    registered_first_outcome_id: int | None
    registered_current: str | None
    registered_current_outcome_id: int | None


def _fetch_registry_outcome(
    conn: psycopg.Connection[Any], nct_id: str, source: str
) -> tuple[str | None, int | None]:
    """(measure text, outcomes.id) for this trial's first PRIMARY outcome
    row under `source` -- `ORDER BY id` tie-break for a trial with more
    than one registered PRIMARY outcome, same convention as
    eval/t4_questions.py's bulk fetch. `(None, None)` when the row doesn't
    exist (e.g. the undocumented history endpoint never returned a version
    0 for this trial) -- never an empty string standing in for "missing.\""""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, measure FROM outcomes WHERE nct_id = %s AND kind = 'PRIMARY' "
            "AND source = %s ORDER BY id LIMIT 1",
            (nct_id, source),
        )
        row = cur.fetchone()
    if row is None:
        return None, None
    return row[1], row[0]


def answer_cross_source_query(
    query: str,
    nct_id: str,
    embedder: Any,
    conn: psycopg.Connection[Any],
    reranker: Any,
    store: TraceStore,
    query_id: int,
    top_k: int = DEFAULT_TOP_K,
) -> CrossSourceAnswer:
    """Runs all three legs for one trial's cross-source comparison and
    returns their raw text plus provenance (chunk ID for the protocol leg,
    outcomes row ID for each registry leg) -- the direct input to S4-05's
    three independent pairwise verdicts, not itself a verdict.

    A leg with nothing retrievable/lookup-able comes back `None`, never an
    empty string: `protocol_leg` is `None` when the `objectives` section is
    unclassified or absent for this trial (expected on the thin
    academic-summary documents `corpus_assessment.md` SS4 flags) so S4-05
    can distinguish "we looked and found nothing" from "we don't know,"
    per `discrepancy_definition.md` SS3's explicit rule that retrieval
    failure is not evidence of divergence."""
    protocol_subquery, first_subquery, current_subquery = decompose_cross_source_query(
        query, nct_id
    )
    assert protocol_subquery.query_text is not None

    reranked_ids = rerank_ladder(
        protocol_subquery.query_text,
        embedder,
        reranker,
        conn,
        store,
        query_id,
        filters=protocol_subquery.filters,
        top_k=top_k,
    )
    protocol_chunk_id = reranked_ids[0] if reranked_ids else None
    protocol_leg = None
    if protocol_chunk_id is not None:
        chunks = fetch_chunks(conn, [protocol_chunk_id])
        protocol_leg = chunks[0].text if chunks else None

    with traced_call(store, query_id, "structured_lookup") as trace:
        first_text, first_id = _fetch_registry_outcome(conn, nct_id, first_subquery.leg)
        trace.filters_applied = f"nct_id={nct_id},source={first_subquery.leg}"

    with traced_call(store, query_id, "structured_lookup") as trace:
        current_text, current_id = _fetch_registry_outcome(conn, nct_id, current_subquery.leg)
        trace.filters_applied = f"nct_id={nct_id},source={current_subquery.leg}"

    return CrossSourceAnswer(
        protocol_leg=protocol_leg,
        protocol_chunk_id=protocol_chunk_id,
        registered_first=first_text,
        registered_first_outcome_id=first_id,
        registered_current=current_text,
        registered_current_outcome_id=current_id,
    )
