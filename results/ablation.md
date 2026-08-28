# Retrieval ladder ablation

Draft table (S3-09) — S3-12 formalizes full auto-generation of this file straight from trace-store
rows (`retrieval_step`/`chunk_hit`), per the acceptance criterion that no number here is ever
hand-copied. For now this reflects one manual run of the first three rungs; rungs 4-5 (S3-10's
metadata prefilter, S3-11's cross-encoder rerank) will be appended as they're built.

**Naming note** (see `retrieval/schema.sql`): Postgres's `tsvector` + `ts_rank_cd` is a
cover-density ranking function, not Okapi BM25. Every "BM25" below means "this project's lexical
leg," not a literal BM25 implementation.

## Setup

- Corpus: full loaded `chunks` table (30,192 chunks, S3-02) — **no metadata prefilter yet**, so
  every query searches across all 200 trials' chunks, not just the trial the question is actually
  about. This is expected to understate recall relative to the realistic single-trial-scoped
  scenario S3-10's prefilter rung will measure.
- Questions: T1 (200) + T2 (101) combined, 301 total, scored in one `score_retrieval_run` pass per
  rung (S3-05).
- k=20 candidates per rung; metrics reported at k∈{1,5,10,20} where applicable.
- Embedding model: `BAAI/bge-base-en-v1.5` (S3-01), query text prefixed with BGE's recommended
  retrieval-query instruction (`retrieval/dense.py::QUERY_INSTRUCTION_PREFIX`).
- Every call traced via `traced_call` (S1-08); hybrid's dense/bm25 legs are separately traced
  sub-stages under the same query, per S3-09's design.

## Results

| Rung | Retriever | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|---|
| 1 | Dense only (`dense_search`) | 0.057 | 0.112 | 0.149 | 0.173 | 0.090 | 0.100 |
| 2 | Lexical only (`lexical_search`, "BM25") | 0.036 | 0.070 | 0.087 | 0.096 | 0.121 | 0.092 |
| 3 | **Hybrid (RRF fusion, S3-09)** | 0.059 | **0.158** | **0.214** | **0.254** | **0.149** | **0.151** |

Precision@k (for reference — expected to be low across the board since each question has only a
handful of true gold chunks out of ~30k candidates):

| Rung | Precision@1 | Precision@5 | Precision@10 | Precision@20 |
|---|---|---|---|---|
| Dense only | 0.060 | 0.024 | 0.016 | 0.010 |
| Lexical only | 0.096 | 0.060 | 0.044 | 0.026 |
| Hybrid (RRF) | 0.066 | 0.055 | 0.045 | 0.031 |

## Reading the numbers

- **Hybrid beats both individual legs at every k** (e.g. Recall@10: 0.149 dense, 0.087 lexical,
  0.214 hybrid) — the expected, and here confirmed, benefit of RRF fusion: dense and lexical search
  surface different relevant chunks, and combining their rankings recovers more of the gold set than
  either alone. This is the core justification for building the hybrid rung at all.
- **Lexical has higher Precision@1 than dense** (0.096 vs. 0.060) despite lower recall — exact
  keyword/phrase matches (drug names, specific numbers, section headings) rank very precisely when
  they hit, but miss paraphrased or semantically-related content dense search would catch instead.
  This is the classic dense/lexical complementarity hybrid retrieval is built to exploit.
- **Absolute recall is still low** (0.21 at k=10) — expected at this stage: there is no metadata
  prefilter yet (S3-10), so every query competes against all 200 trials' chunks instead of just the
  one trial it's actually about. S3-10's prefilter rung should show a substantial jump for exactly
  this reason; if it doesn't, that's a signal worth investigating rather than assuming prefiltering
  trivially helps.

## Latency (traced, not yet broken out per-stage in this draft)

Full per-stage latency (embed / dense / bm25 / fuse / prefilter / rerank / generate) is deferred to
S3-12's formal ablation report, which reads it directly from `retrieval_step.latency_ms` grouped by
`stage` — this draft only ran one full pass per rung to establish the ladder is climbing correctly,
not to characterize latency yet.
