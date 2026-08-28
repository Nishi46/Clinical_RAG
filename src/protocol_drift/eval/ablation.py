"""Ablation runs -- S3-12.

Runs every ladder rung (S3-01/S3-02's dense-only floor through S3-11's
cross-encoder rerank) over the full T1+T2 question set, entirely through
traced calls, and renders `results/ablation.md` by reading the numbers
back out: Recall/Precision/MRR/nDCG come from the real `score_retrieval_run`
math (S3-05) computed during the run, and per-stage latency is aggregated
live from `retrieval_step.latency_ms` -- nothing in the rendered table is
hand-typed.

`run_rung` does its own per-question loop rather than delegating to
`score_retrieval_run` as a black box, because it needs retrieval and
generation for the *same* question to share one `query_id` (so a query's
full pipeline -- prefilter/dense/bm25/rerank/generate/judge -- reads back
as one coherent trace), which `score_retrieval_run` alone can't provide
since it never exposes the query_id it creates internally.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

import psycopg

from protocol_drift.db import DEFAULT_DSN
from protocol_drift.eval.correctness_scorer import (
    exact_match_score,
    faithfulness_score,
    judged_correctness,
)
from protocol_drift.eval.models import EvalQuestion
from protocol_drift.eval.retrieval_scorer import (
    RetrievalScores,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from protocol_drift.generation.answer import generate_answer
from protocol_drift.retrieval.types import fetch_chunks
from protocol_drift.trace.store import TraceStore

DEFAULT_KS: tuple[int, ...] = (1, 5, 10, 20)
STAGES_BY_PIPELINE_DEPTH = ("prefilter", "dense", "bm25", "rrf", "rerank")


@dataclass
class RungResult:
    name: str
    retrieval: RetrievalScores
    correctness_t1: float | None  # mean exact_match over T1 (1.0/0.0 per question)
    correctness_t2: float | None  # mean judged score over T2 (None-scores excluded)
    faithfulness: float | None  # mean grounded-claim ratio over answered questions
    query_id_min: int
    query_id_max: int


@dataclass
class AblationReport:
    rungs: list[RungResult] = field(default_factory=list)


def _max_query_id(conn: psycopg.Connection[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM query")
        row = cur.fetchone()
    return row[0] if row else 0


def run_rung(
    name: str,
    retrieve_fn: Callable[[str, int], list[str]],
    questions: Sequence[EvalQuestion],
    store: TraceStore,
    conn: psycopg.Connection[Any],
    generate_answers: bool = True,
    ks: tuple[int, ...] = DEFAULT_KS,
) -> RungResult:
    """`retrieve_fn` must fully self-trace (S3-09's hybrid_search and
    S3-11's rerank_ladder both do) -- this function does not wrap it in an
    extra traced_call of its own, for the same reason `score_retrieval_run`
    stopped doing so once real multi-stage retrievers existed (S3-11)."""
    query_id_min = _max_query_id(conn) + 1

    recall_sums = dict.fromkeys(ks, 0.0)
    precision_sums = dict.fromkeys(ks, 0.0)
    rr_sum = 0.0
    ndcg_sum = 0.0
    correctness_t1_scores: list[float] = []
    correctness_t2_scores: list[float] = []
    faithfulness_scores: list[float] = []

    for question in questions:
        is_t1 = question.template_id is not None
        tier = "T1" if is_t1 else "T2"
        query_id = store.log_query(question.question_text, tier=tier)

        retrieved = retrieve_fn(question.question_text, query_id)

        gold = question.gold_chunk_ids
        for k in ks:
            recall_sums[k] += recall_at_k(retrieved, gold, k)
            precision_sums[k] += precision_at_k(retrieved, gold, k)
        rr_sum += mrr(retrieved, gold)
        ndcg_sum += ndcg_at_k(retrieved, gold, k=10)

        if generate_answers:
            chunks = fetch_chunks(conn, retrieved)
            answer = generate_answer(question, chunks, store, tier=tier, query_id=query_id)

            if is_t1:
                correctness_t1_scores.append(
                    1.0 if exact_match_score(answer.response_text, question.gold_answer) else 0.0
                )
            elif question.gold_answer_notes is not None:
                score, _ = judged_correctness(
                    question, answer.response_text, question.gold_answer_notes, query_id, store
                )
                if score is not None:
                    correctness_t2_scores.append(score)

            if not answer.is_refusal:
                faith = faithfulness_score(answer.response_text, chunks, query_id, store)
                faithfulness_scores.append(faith.score)

    n = len(questions)
    query_id_max = _max_query_id(conn)

    retrieval = RetrievalScores(
        n_questions=n,
        recall_at_k={k: v / n for k, v in recall_sums.items()} if n else dict.fromkeys(ks, 0.0),
        precision_at_k=(
            {k: v / n for k, v in precision_sums.items()} if n else dict.fromkeys(ks, 0.0)
        ),
        mrr=rr_sum / n if n else 0.0,
        ndcg_at_10=ndcg_sum / n if n else 0.0,
    )

    return RungResult(
        name=name,
        retrieval=retrieval,
        correctness_t1=mean(correctness_t1_scores) if correctness_t1_scores else None,
        correctness_t2=mean(correctness_t2_scores) if correctness_t2_scores else None,
        faithfulness=mean(faithfulness_scores) if faithfulness_scores else None,
        query_id_min=query_id_min,
        query_id_max=query_id_max,
    )


def run_ablation(
    rungs: Sequence[tuple[str, Callable[[str, int], list[str]]]],
    questions: Sequence[EvalQuestion],
    store: TraceStore,
    conn: psycopg.Connection[Any],
    generate_answers: bool = True,
    ks: tuple[int, ...] = DEFAULT_KS,
) -> AblationReport:
    results = [
        run_rung(name, fn, questions, store, conn, generate_answers=generate_answers, ks=ks)
        for name, fn in rungs
    ]
    return AblationReport(rungs=results)


def _stage_latency_ms(
    conn: psycopg.Connection[Any], query_id_min: int, query_id_max: int
) -> dict[str, dict[str, float]]:
    """p50/p95/mean latency per stage, straight from retrieval_step rows
    for this rung's query_id range -- the only place per-stage latency
    lives; RetrievalScores has no latency field of its own."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stage, "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50, "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95, "
            "avg(latency_ms) AS mean, count(*) AS n "
            "FROM retrieval_step "
            "WHERE query_id BETWEEN %s AND %s "
            "GROUP BY stage",
            (query_id_min, query_id_max),
        )
        rows = cur.fetchall()
    return {
        stage: {"p50": p50, "p95": p95, "mean": avg, "n": n} for stage, p50, p95, avg, n in rows
    }


def render_ablation_md(report: AblationReport, conn: psycopg.Connection[Any]) -> str:
    lines = ["# Retrieval ladder ablation", ""]
    lines.append(
        "Auto-generated by `scripts/run_ablation.py` (`make ablation`) -- every number below is "
        "read from the trace store or computed live during the run; none is hand-typed. Re-running "
        "this script regenerates this file from scratch."
    )
    lines.append("")
    lines.append(
        "**Naming note** (see `retrieval/schema.sql`): Postgres's `tsvector` + `ts_rank_cd` is a "
        "cover-density ranking function, not Okapi BM25. Every \"BM25\" below means \"this "
        "project's lexical leg,\" not a literal BM25 implementation."
    )
    lines.append("")
    lines.append("## Retrieval quality")
    lines.append("")
    ks = sorted(report.rungs[0].retrieval.recall_at_k) if report.rungs else []
    header = "| Rung | " + " | ".join(f"Recall@{k}" for k in ks) + " | MRR | nDCG@10 |"
    lines.append(header)
    lines.append("|" + "---|" * (len(ks) + 3))
    for rung in report.rungs:
        recalls = " | ".join(f"{rung.retrieval.recall_at_k[k]:.3f}" for k in ks)
        lines.append(
            f"| {rung.name} | {recalls} | {rung.retrieval.mrr:.3f} | "
            f"{rung.retrieval.ndcg_at_10:.3f} |"
        )
    lines.append("")

    header = "| Rung | " + " | ".join(f"Precision@{k}" for k in ks) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(ks) + 1))
    for rung in report.rungs:
        precisions = " | ".join(f"{rung.retrieval.precision_at_k[k]:.3f}" for k in ks)
        lines.append(f"| {rung.name} | {precisions} |")
    lines.append("")

    lines.append("## Generation quality")
    lines.append("")
    lines.append(
        "T1 correctness is exact/normalized match (no model call); T2 correctness is the judge's "
        "0/0.5/1 score (see `docs/judge_calibration.md` for the judge's own reliability -- "
        "κ=0.380, treat T2 correctness here as a noisy signal, not ground truth). Faithfulness is "
        "the atomic-claim grounded ratio, over non-refusal answers only."
    )
    lines.append("")
    lines.append("| Rung | T1 correctness | T2 correctness (judged) | Faithfulness |")
    lines.append("|---|---|---|---|")
    for rung in report.rungs:
        t1 = f"{rung.correctness_t1:.3f}" if rung.correctness_t1 is not None else "n/a"
        t2 = f"{rung.correctness_t2:.3f}" if rung.correctness_t2 is not None else "n/a"
        faith = f"{rung.faithfulness:.3f}" if rung.faithfulness is not None else "n/a"
        lines.append(f"| {rung.name} | {t1} | {t2} | {faith} |")
    lines.append("")

    lines.append("## Latency per stage (ms), read live from `retrieval_step`")
    lines.append("")
    lines.append("| Rung | Stage | n | p50 | p95 | mean |")
    lines.append("|---|---|---|---|---|---|")
    for rung in report.rungs:
        stage_latency = _stage_latency_ms(conn, rung.query_id_min, rung.query_id_max)
        for stage in STAGES_BY_PIPELINE_DEPTH:
            if stage not in stage_latency:
                continue
            stats = stage_latency[stage]
            lines.append(
                f"| {rung.name} | {stage} | {stats['n']:.0f} | {stats['p50']:.1f} | "
                f"{stats['p95']:.1f} | {stats['mean']:.1f} |"
            )
    lines.append("")

    return "\n".join(lines)


DEFAULT_T1_PATH = Path("data/eval/t1.jsonl")
DEFAULT_T2_PATH = Path("data/eval/t2.jsonl")
DEFAULT_OUTPUT_PATH = Path("results/ablation.md")
DEFAULT_RUNG_K = 20


def _load_questions(t1_path: Path, t2_path: Path) -> list[EvalQuestion]:
    rows = [
        json.loads(line)
        for path in (t1_path, t2_path)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    return [
        EvalQuestion(
            question_id=row["question_id"],
            nct_id=row["nct_id"],
            question_text=row["question_text"],
            gold_answer=row["gold_answer"],
            gold_chunk_ids=row["gold_chunk_ids"],
            template_id=row.get("template_id"),
            gold_answer_notes=row.get("gold_answer_notes"),
        )
        for row in rows
    ]


def _build_rungs(
    questions: Sequence[EvalQuestion],
    embedder: Any,
    reranker: Any,
    conn: psycopg.Connection[Any],
    store: TraceStore,
    rung_k: int,
) -> list[tuple[str, Callable[[str, int], list[str]]]]:
    # retrieve_fn's contract is (question_text, query_id) -- it never
    # receives the EvalQuestion itself, but rungs 4-5's prefilter needs
    # question.nct_id. All question texts are unique (verified when T1/T2
    # were generated), so this lookup is safe.
    from protocol_drift.retrieval.dense import dense_search, embed_query
    from protocol_drift.retrieval.hybrid import hybrid_search
    from protocol_drift.retrieval.lexical import lexical_search
    from protocol_drift.retrieval.query_parse import QueryFilters
    from protocol_drift.retrieval.rerank import rerank_ladder
    from protocol_drift.trace.store import traced_call

    nct_id_by_text = {q.question_text: q.nct_id for q in questions}

    def dense_only(query_text: str, query_id: int) -> list[str]:
        with traced_call(store, query_id, "dense") as trace:
            query_embedding = embed_query(query_text, embedder)
            results = dense_search(query_embedding, rung_k, conn)
            trace.chunk_hits = [
                {"chunk_id": chunk_id, "rank": rank, "score": score}
                for rank, (chunk_id, score) in enumerate(results)
            ]
        return [chunk_id for chunk_id, _ in results]

    def lexical_only(query_text: str, query_id: int) -> list[str]:
        with traced_call(store, query_id, "bm25") as trace:
            results = lexical_search(query_text, rung_k, conn)
            trace.chunk_hits = [
                {"chunk_id": chunk_id, "rank": rank, "score": score}
                for rank, (chunk_id, score) in enumerate(results)
            ]
        return [chunk_id for chunk_id, _ in results]

    def hybrid(query_text: str, query_id: int) -> list[str]:
        return hybrid_search(query_text, rung_k, embedder, conn, store, query_id)

    def hybrid_prefiltered(query_text: str, query_id: int) -> list[str]:
        filters = QueryFilters(nct_id=nct_id_by_text[query_text])
        return hybrid_search(query_text, rung_k, embedder, conn, store, query_id, filters=filters)

    def reranked(query_text: str, query_id: int) -> list[str]:
        filters = QueryFilters(nct_id=nct_id_by_text[query_text])
        return rerank_ladder(query_text, embedder, reranker, conn, store, query_id, filters=filters)

    return [
        ("1. Dense only", dense_only),
        ("2. Lexical only (BM25)", lexical_only),
        ("3. Hybrid (RRF)", hybrid),
        ("4. Hybrid + prefilter", hybrid_prefiltered),
        ("5. + cross-encoder rerank", reranked),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full retrieval ladder ablation.")
    parser.add_argument("--t1", type=Path, default=DEFAULT_T1_PATH)
    parser.add_argument("--t2", type=Path, default=DEFAULT_T2_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--rung-k", type=int, default=DEFAULT_RUNG_K)
    parser.add_argument(
        "--no-generate",
        action="store_true",
        help="retrieval-only: skip generation/correctness/faithfulness scoring",
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

    questions = _load_questions(args.t1, args.t2)
    print(f"Loaded {len(questions)} questions ({args.t1}, {args.t2})")

    conn = psycopg.connect(args.dsn)
    store = TraceStore(conn)

    print("Loading embedder...")
    embedder = load_embedder(EMBED_MODEL_NAME, EMBED_MODEL_REVISION)
    print("Loading reranker...")
    reranker = load_reranker(RERANK_MODEL_NAME, RERANK_MODEL_REVISION)

    rungs = _build_rungs(questions, embedder, reranker, conn, store, args.rung_k)

    generate_answers = not args.no_generate
    print(
        f"Running {len(rungs)} rungs over {len(questions)} questions "
        f"(generate_answers={generate_answers})..."
    )
    report = run_ablation(rungs, questions, store, conn, generate_answers=generate_answers)

    rendered = render_ablation_md(report, conn)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    print(f"Wrote {args.output}")

    conn.close()


if __name__ == "__main__":
    main()
