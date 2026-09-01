"""Discrepancy detector -- S4-05. The core deliverable, depending on
S4-03's `compare_outcomes` (normalization) and S4-04's
`answer_cross_source_query` (decomposition).

`detect_discrepancies` runs `compare_outcomes` on each of the three pairs
from `discrepancy_definition.md` SS2's table **separately** and returns a
`DiscrepancyReport` carrying three independent `PairVerdict`s -- never one
collapsed flag, per that doc's explicit requirement. Every verdict carries
citations to every source it compared (`outcomes` row IDs, a protocol
chunk ID), and a pair that genuinely doesn't apply to a trial (no
`results_reported` row, no registry data at all) comes back `None` in
`DiscrepancyReport.pairs`, distinguished from a `retrieval_failed=True`
verdict (S4-04 looked for the protocol leg and found nothing) and from an
`ambiguous` normalization verdict (the judge looked at both texts and
couldn't confidently classify) -- three different kinds of "no clean
verdict," never conflated.

`DiscrepancyReport.to_dict()` / `PairVerdict.to_dict()` serialize to
exactly the `data/discrepancy/reports/{nct_id}.json` shape
`eval/discrepancy_scorer.py`'s `load_detector_reports` already expects
(written before this module existed, anticipating this contract) -- a
`pairs` map keyed by pair type to `{"verdict", "retrieval_failed", ...}`
or `null` for a not-applicable pair.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg

from protocol_drift.normalize.outcome import Verdict, compare_outcomes
from protocol_drift.retrieval.decompose import CrossSourceAnswer, answer_cross_source_query
from protocol_drift.trace.store import TraceStore

PairType = Literal["first_posted_vs_current", "current_vs_protocol", "registry_vs_results"]

PAIR_TYPES: tuple[PairType, ...] = (
    "first_posted_vs_current",
    "current_vs_protocol",
    "registry_vs_results",
)


# --- render_verdict_text: plain structural language only --------------------
#
# Deliberately templated, never a quote of the judge's own justification
# text -- per discrepancy_definition.md SS4's ethics stance ("never present
# a flagged trial-level divergence with language implying intent, bias, or
# wrongdoing"), a fixed template can't accidentally drift into loaded
# language the way freeform judge output could (S4-03's own real eval
# found the judge only 65% accurate against the hand-labeled set, so its
# raw prose isn't something to surface unfiltered as this project's voice
# anyway). This also makes the denylist guardrail test below deterministic
# rather than dependent on live model output.

_PAIR_LABELS: dict[PairType, tuple[str, str]] = {
    "first_posted_vs_current": ("current registry", "first-posted registry"),
    "current_vs_protocol": ("current registry", "protocol document"),
    "registry_vs_results": ("registry", "results-reported"),
}


def render_verdict_text(verdict: PairVerdict) -> str:
    a_label, b_label = _PAIR_LABELS[verdict.pair]
    if verdict.retrieval_failed:
        return (
            f"{b_label} primary outcome could not be retrieved for this trial -- "
            "no comparison was made."
        )
    if verdict.verdict == "match":
        return f"{a_label} primary outcome matches {b_label} primary outcome."
    if verdict.verdict == "divergence":
        return f"{a_label} primary outcome differs from {b_label} primary outcome."
    if verdict.verdict == "ambiguous":
        return (
            f"{a_label} primary outcome could not be confidently compared to {b_label} "
            "primary outcome -- flagged for human review."
        )
    raise ValueError(f"PairVerdict with no verdict and retrieval_failed=False: {verdict}")


@dataclass
class PairVerdict:
    pair: PairType
    verdict: Verdict | None  # None only when retrieval_failed
    retrieval_failed: bool
    method: (
        str | None
    )  # compare_outcomes' method: "identical_text"/"timeframe_deterministic"/"judge"
    citations: dict[str, int | str | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "retrieval_failed": self.retrieval_failed,
            "method": self.method,
            "citations": self.citations,
            "verdict_text": render_verdict_text(self),
        }


@dataclass
class DiscrepancyReport:
    nct_id: str
    pairs: dict[PairType, PairVerdict | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nct_id": self.nct_id,
            "pairs": {
                pair: (verdict.to_dict() if verdict is not None else None)
                for pair, verdict in self.pairs.items()
            },
        }


def _fetch_results_reported(
    conn: psycopg.Connection[Any], nct_id: str
) -> tuple[str | None, int | None]:
    """(measure text, outcomes.id) for this trial's first PRIMARY
    results-reported outcome, or `(None, None)` when absent -- expected
    for a trial with no posted results (`hasResults=False`), per
    `discrepancy_definition.md` SS2, not an error. There is no
    `trials.has_results` column (only `has_protocol`/`has_sap` exist,
    S1-05) -- row presence/absence in `outcomes` *is* the hasResults
    signal, since S1-05's extract.py only ever inserts a
    `source='results_reported'` row when the registry's `resultsSection`
    was actually present."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, measure FROM outcomes WHERE nct_id = %s AND kind = 'PRIMARY' "
            "AND source = 'results_reported' ORDER BY id LIMIT 1",
            (nct_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None, None
    return row[1], row[0]


def _pairwise_verdict(
    pair: PairType,
    text_a: str,
    text_b: str,
    query_id: int,
    store: TraceStore,
    citations: dict[str, int | str | None],
) -> PairVerdict:
    result = compare_outcomes(text_a, text_b, query_id, store)
    return PairVerdict(
        pair=pair,
        verdict=result.verdict,
        retrieval_failed=False,
        method=result.method,
        citations=citations,
    )


def detect_discrepancies(
    nct_id: str,
    conn: psycopg.Connection[Any],
    embedder: Any,
    reranker: Any,
    store: TraceStore,
    query_id: int,
) -> DiscrepancyReport:
    """Runs S4-04's `answer_cross_source_query` for this trial's primary
    outcome, then grades each of the three pairs from
    `discrepancy_definition.md` SS2's table independently. A pair is
    `None` in the returned report when it genuinely doesn't apply to this
    trial (missing registry data, no results reported) -- distinct from a
    graded `PairVerdict` with `retrieval_failed=True` (S4-04 looked and
    found nothing retrievable)."""
    cross_source: CrossSourceAnswer = answer_cross_source_query(
        f"What is the primary outcome for {nct_id}?",
        nct_id,
        embedder,
        conn,
        reranker,
        store,
        query_id,
    )

    pairs: dict[PairType, PairVerdict | None] = {}

    # first_posted_vs_current: both sides already structured Postgres rows,
    # no retrieval dependency at all.
    if cross_source.registered_first is not None and cross_source.registered_current is not None:
        pairs["first_posted_vs_current"] = _pairwise_verdict(
            "first_posted_vs_current",
            cross_source.registered_first,
            cross_source.registered_current,
            query_id,
            store,
            {
                "registered_first_outcome_id": cross_source.registered_first_outcome_id,
                "registered_current_outcome_id": cross_source.registered_current_outcome_id,
            },
        )
    else:
        pairs["first_posted_vs_current"] = None

    # current_vs_protocol: needs the S4-04 protocol leg -- a retrieval
    # failure here (objectives section unclassified/absent) is reported as
    # its own graded state, never silently dropped and never defaulted to
    # divergence.
    if cross_source.registered_current is None:
        pairs["current_vs_protocol"] = None
    elif cross_source.protocol_leg is None:
        pairs["current_vs_protocol"] = PairVerdict(
            pair="current_vs_protocol",
            verdict=None,
            retrieval_failed=True,
            method=None,
            citations={
                "registered_current_outcome_id": cross_source.registered_current_outcome_id,
                "protocol_chunk_id": None,
            },
        )
    else:
        pairs["current_vs_protocol"] = _pairwise_verdict(
            "current_vs_protocol",
            cross_source.registered_current,
            cross_source.protocol_leg,
            query_id,
            store,
            {
                "registered_current_outcome_id": cross_source.registered_current_outcome_id,
                "protocol_chunk_id": cross_source.protocol_chunk_id,
            },
        )

    # registry_vs_results: registered_current is "the registry" side
    # (registered_first is cited alongside it when available, per SS2's
    # "first-posted and/or current" framing, but current is the more
    # contemporaneous of the two and is what's compared). Absent when
    # this trial has no results_reported row -- expected, not an error.
    results_text, results_id = _fetch_results_reported(conn, nct_id)
    registry_text = cross_source.registered_current or cross_source.registered_first
    registry_outcome_id = (
        cross_source.registered_current_outcome_id or cross_source.registered_first_outcome_id
    )
    if results_text is None or registry_text is None:
        pairs["registry_vs_results"] = None
    else:
        pairs["registry_vs_results"] = _pairwise_verdict(
            "registry_vs_results",
            registry_text,
            results_text,
            query_id,
            store,
            {
                "registered_first_outcome_id": cross_source.registered_first_outcome_id,
                "registry_outcome_id": registry_outcome_id,
                "results_reported_outcome_id": results_id,
            },
        )

    return DiscrepancyReport(nct_id=nct_id, pairs=pairs)
