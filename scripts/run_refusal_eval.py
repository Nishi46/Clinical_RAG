#!/usr/bin/env python3
"""S4-09 steps 3-4 driver: runs the real rerank_ladder + generate_answer
pipeline over the adversarial set (`data/eval/adversarial.jsonl`) and a
sample of already-answerable T1/T2 questions, computes `refusal_metrics`,
and writes `docs/refusal_eval.md`. Real embedder/reranker/judge model,
real Postgres, real trace store -- nothing here is a stub.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg

from protocol_drift.db import DEFAULT_DSN
from protocol_drift.eval.adversarial_questions import (
    AdversarialQuestion,
    refusal_metrics,
    render_refusal_eval_md,
)
from protocol_drift.eval.models import EvalQuestion
from protocol_drift.generation.answer import generate_answer
from protocol_drift.retrieval.query_parse import QueryFilters
from protocol_drift.retrieval.rerank import rerank_ladder
from protocol_drift.retrieval.types import fetch_chunks
from protocol_drift.trace.store import TraceStore

DEFAULT_ADVERSARIAL_PATH = Path("data/eval/adversarial.jsonl")
DEFAULT_T1_PATH = Path("data/eval/t1.jsonl")
DEFAULT_T2_PATH = Path("data/eval/t2.jsonl")
DEFAULT_OUTPUT_PATH = Path("docs/refusal_eval.md")
DEFAULT_ANSWERABLE_SAMPLE_SIZE = 30


def _load_adversarial(path: Path) -> list[AdversarialQuestion]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [AdversarialQuestion(**row) for row in rows]


def _load_eval_questions(path: Path) -> list[EvalQuestion]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
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


def _make_generate_fn(
    conn: psycopg.Connection[Any],
    embedder: Any,
    reranker: Any,
    store: TraceStore,
    nct_id_by_text: dict[str, str],
) -> Callable[[str], str]:
    def generate_fn(question_text: str) -> str:
        nct_id = nct_id_by_text[question_text]
        query_id = store.log_query(question_text)
        filters = QueryFilters(nct_id=nct_id)
        chunk_ids = rerank_ladder(
            question_text, embedder, reranker, conn, store, query_id, filters=filters
        )
        chunks = fetch_chunks(conn, chunk_ids)
        question = EvalQuestion(
            question_id="refusal-eval-adhoc",
            nct_id=nct_id,
            question_text=question_text,
            gold_answer="",
            gold_chunk_ids=[],
        )
        answer = generate_answer(question, chunks, store, query_id=query_id)
        return answer.response_text

    return generate_fn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run S4-09's refusal evaluation.")
    parser.add_argument("--adversarial", type=Path, default=DEFAULT_ADVERSARIAL_PATH)
    parser.add_argument("--t1", type=Path, default=DEFAULT_T1_PATH)
    parser.add_argument("--t2", type=Path, default=DEFAULT_T2_PATH)
    parser.add_argument(
        "--answerable-sample-size", type=int, default=DEFAULT_ANSWERABLE_SAMPLE_SIZE
    )
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--seed", type=int, default=0)
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

    adversarial = _load_adversarial(args.adversarial)
    print(f"Loaded {len(adversarial)} adversarial question(s)")

    t1_and_t2 = _load_eval_questions(args.t1) + _load_eval_questions(args.t2)
    rng = random.Random(args.seed)
    answerable_sample = rng.sample(t1_and_t2, k=min(args.answerable_sample_size, len(t1_and_t2)))
    print(f"Sampled {len(answerable_sample)} already-answerable T1/T2 question(s)")

    nct_id_by_text = {q.question_text: q.nct_id for q in adversarial}
    nct_id_by_text.update({q.question_text: q.nct_id for q in answerable_sample})

    conn = psycopg.connect(args.dsn)
    store = TraceStore(conn)

    print("Loading embedder...")
    embedder = load_embedder(EMBED_MODEL_NAME, EMBED_MODEL_REVISION)
    print("Loading reranker...")
    reranker = load_reranker(RERANK_MODEL_NAME, RERANK_MODEL_REVISION)

    generate_fn = _make_generate_fn(conn, embedder, reranker, store, nct_id_by_text)

    print("Running refusal metrics (this calls the real retrieval + generation pipeline)...")
    scores = refusal_metrics(adversarial, answerable_sample, generate_fn)
    print(
        f"  refusal_accuracy={scores.refusal_accuracy:.3f} "
        f"over_refusal_rate={scores.over_refusal_rate:.3f}"
    )

    rendered = render_refusal_eval_md(scores)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    print(f"Wrote {args.output}")

    conn.close()


if __name__ == "__main__":
    main()
