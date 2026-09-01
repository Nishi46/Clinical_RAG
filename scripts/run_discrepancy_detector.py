#!/usr/bin/env python3
"""S4-05 step 5 driver: runs `detect_discrepancies` over every cohort
trial and writes `data/discrepancy/reports/{nct_id}.json` -- the shape
`eval/discrepancy_scorer.py`'s `load_detector_reports` (S4-08) already
expects. Real embedder/reranker, real Postgres, real (cached where
possible) judge calls -- nothing here is a stub.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg

from protocol_drift.db import DEFAULT_DSN
from protocol_drift.discrepancy.detector import DiscrepancyReport, detect_discrepancies
from protocol_drift.trace.store import TraceStore

DEFAULT_COHORT_PATH = Path("data/cohort.json")
DEFAULT_OUTPUT_DIR = Path("data/discrepancy/reports")


def run_cohort(
    cohort: dict[str, Any],
    conn: psycopg.Connection[Any],
    embedder: Any,
    reranker: Any,
    store: TraceStore,
    output_dir: Path,
) -> list[DiscrepancyReport]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for i, trial in enumerate(cohort["trials"], start=1):
        nct_id = trial["nct_id"]
        query_id = store.log_query(f"discrepancy_detection:{nct_id}")
        report = detect_discrepancies(nct_id, conn, embedder, reranker, store, query_id)
        reports.append(report)

        (output_dir / f"{nct_id}.json").write_text(json.dumps(report.to_dict(), indent=2))
        pair_summary = {
            pair: ("n/a" if v is None else "retrieval_failed" if v.retrieval_failed else v.verdict)
            for pair, v in report.pairs.items()
        }
        print(f"[{i}/{len(cohort['trials'])}] {nct_id}: {pair_summary}")

    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the discrepancy detector over the cohort.")
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT_PATH)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--limit", type=int, default=None, help="process only the first N cohort trials"
    )
    args = parser.parse_args()

    from protocol_drift.retrieval.embed import (
        DEFAULT_MODEL_NAME as EMBED_MODEL_NAME,
    )
    from protocol_drift.retrieval.embed import (
        DEFAULT_MODEL_REVISION as EMBED_MODEL_REVISION,
    )
    from protocol_drift.retrieval.embed import load_embedder
    from protocol_drift.retrieval.rerank import (
        DEFAULT_MODEL_NAME as RERANK_MODEL_NAME,
    )
    from protocol_drift.retrieval.rerank import (
        DEFAULT_MODEL_REVISION as RERANK_MODEL_REVISION,
    )
    from protocol_drift.retrieval.rerank import load_reranker

    cohort = json.loads(args.cohort.read_text())
    if args.limit is not None:
        cohort["trials"] = cohort["trials"][: args.limit]
    print(f"Loaded {len(cohort['trials'])} trials from {args.cohort}")

    conn = psycopg.connect(args.dsn)
    store = TraceStore(conn)

    print("Loading embedder...")
    embedder = load_embedder(EMBED_MODEL_NAME, EMBED_MODEL_REVISION)
    print("Loading reranker...")
    reranker = load_reranker(RERANK_MODEL_NAME, RERANK_MODEL_REVISION)

    reports = run_cohort(cohort, conn, embedder, reranker, store, args.output_dir)

    n_with_protocol_pair = sum(1 for r in reports if r.pairs["current_vs_protocol"] is not None)
    print(
        f"Wrote {len(reports)} report(s) -> {args.output_dir} "
        f"({n_with_protocol_pair} with a locatable protocol endpoint)"
    )
    conn.close()


if __name__ == "__main__":
    main()
