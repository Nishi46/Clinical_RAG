"""Retrieval scorer -- S3-05.

Plain-Python Recall@k / Precision@k / MRR / nDCG@k -- small enough not to
justify a new dependency like `pytrec_eval`. `score_retrieval_run` drives an
arbitrary `retrieve_fn` over a question set and traces every call through
S1-08's `TraceStore`, so S3-12's ablation table can be regenerated straight
from trace rows instead of hand-typed numbers.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from protocol_drift.eval.models import EvalQuestion
from protocol_drift.trace.store import TraceStore, traced_call


def recall_at_k(retrieved_chunk_ids: Sequence[str], gold_chunk_ids: Sequence[str], k: int) -> float:
    """Fraction of gold chunks found anywhere in the top k retrieved."""
    if not gold_chunk_ids:
        return 0.0
    gold = set(gold_chunk_ids)
    top_k = set(retrieved_chunk_ids[:k])
    return len(top_k & gold) / len(gold)


def precision_at_k(
    retrieved_chunk_ids: Sequence[str], gold_chunk_ids: Sequence[str], k: int
) -> float:
    """Fraction of the top k retrieved slots that are gold. Divides by `k`
    itself (the textbook definition), not `min(k, len(retrieved))` -- a
    retrieve_fn returning fewer than k results is scored as if the missing
    slots are non-relevant, rather than silently inflating its precision."""
    if k <= 0:
        return 0.0
    gold = set(gold_chunk_ids)
    hits = sum(1 for chunk_id in retrieved_chunk_ids[:k] if chunk_id in gold)
    return hits / k


def mrr(retrieved_chunk_ids: Sequence[str], gold_chunk_ids: Sequence[str]) -> float:
    """Reciprocal rank of the first retrieved chunk that's in gold_chunk_ids
    (1-indexed), or 0.0 if none is found. This is the per-question building
    block of Mean Reciprocal Rank -- average it across a question set (as
    `score_retrieval_run` does) to get the actual MRR."""
    gold = set(gold_chunk_ids)
    for rank, chunk_id in enumerate(retrieved_chunk_ids, start=1):
        if chunk_id in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    retrieved_chunk_ids: Sequence[str], gold_chunk_ids: Sequence[str], k: int = 10
) -> float:
    """Binary-relevance nDCG@k: DCG uses the standard log2(rank+1) discount;
    IDCG is the DCG of the ideal ranking (all `min(len(gold), k)` relevant
    chunks placed first). 0.0 if there's no gold to rank against."""
    gold = set(gold_chunk_ids)
    if not gold:
        return 0.0

    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, chunk_id in enumerate(retrieved_chunk_ids[:k], start=1)
        if chunk_id in gold
    )
    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


@dataclass
class RetrievalScores:
    n_questions: int
    recall_at_k: dict[int, float]
    precision_at_k: dict[int, float]
    mrr: float
    ndcg_at_10: float


def score_retrieval_run(
    questions: Sequence[EvalQuestion],
    retrieve_fn: Callable[[str], list[str]],
    store: TraceStore,
    stage: str,
    ks: tuple[int, ...] = (1, 5, 10, 20),
    tier: str | None = None,
) -> RetrievalScores:
    """Runs `retrieve_fn(question.question_text) -> list[chunk_id]` for
    every question, scores it against `gold_chunk_ids`, and aggregates mean
    Recall@k / Precision@k / MRR / nDCG@10 across the set. Every call is
    wrapped in `traced_call(store, query_id, stage)` so each question
    writes a full query + retrieval_step + chunk_hit trace -- `stage` must
    be one of the trace schema's allowed values ('dense', 'bm25', 'rrf',
    'prefilter', 'rerank'); for a bare retrieve_fn with no internal staging
    of its own (e.g. this scorer's first use, against a dense-only
    baseline), pass "dense"."""
    recall_sums = dict.fromkeys(ks, 0.0)
    precision_sums = dict.fromkeys(ks, 0.0)
    rr_sum = 0.0
    ndcg_sum = 0.0

    for question in questions:
        query_id = store.log_query(question.question_text, tier=tier)
        with traced_call(store, query_id, stage) as trace:
            retrieved = retrieve_fn(question.question_text)
            trace.chunk_hits = [
                {"chunk_id": chunk_id, "rank": rank} for rank, chunk_id in enumerate(retrieved)
            ]

        gold = question.gold_chunk_ids
        for k in ks:
            recall_sums[k] += recall_at_k(retrieved, gold, k)
            precision_sums[k] += precision_at_k(retrieved, gold, k)
        rr_sum += mrr(retrieved, gold)
        ndcg_sum += ndcg_at_k(retrieved, gold, k=10)

    n = len(questions)
    if n == 0:
        return RetrievalScores(
            n_questions=0,
            recall_at_k=dict.fromkeys(ks, 0.0),
            precision_at_k=dict.fromkeys(ks, 0.0),
            mrr=0.0,
            ndcg_at_10=0.0,
        )
    return RetrievalScores(
        n_questions=n,
        recall_at_k={k: v / n for k, v in recall_sums.items()},
        precision_at_k={k: v / n for k, v in precision_sums.items()},
        mrr=rr_sum / n,
        ndcg_at_10=ndcg_sum / n,
    )
