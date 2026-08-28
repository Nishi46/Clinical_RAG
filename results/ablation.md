# Retrieval ladder ablation

Draft table (S3-09 through S3-11) — S3-12 formalizes full auto-generation of this file straight from
trace-store rows (`retrieval_step`/`chunk_hit`), per the acceptance criterion that no number here is
ever hand-copied. For now this reflects one manual run of all five ladder rungs.

**Naming note** (see `retrieval/schema.sql`): Postgres's `tsvector` + `ts_rank_cd` is a
cover-density ranking function, not Okapi BM25. Every "BM25" below means "this project's lexical
leg," not a literal BM25 implementation.

## Setup

- Corpus: full loaded `chunks` table (30,192 chunks, S3-02). Rungs 1-3 search across all 200 trials'
  chunks with no restriction; rungs 4-5 restrict to the trial the question is actually about.
- Questions: T1 (200) + T2 (101) combined, 301 total, scored in one `score_retrieval_run` pass per
  rung (S3-05).
- k=20 candidates per rung for rungs 1-4; rung 5 retrieves 50 hybrid candidates (`DEFAULT_CANDIDATE_K`)
  and reranks down to a final top_k=8 (`DEFAULT_TOP_K`) — this is why rung 5's Recall@10 and
  Recall@20 are numerically identical (see "Reading the numbers").
- Embedding model: `BAAI/bge-base-en-v1.5` (S3-01), query text prefixed with BGE's recommended
  retrieval-query instruction (`retrieval/dense.py::QUERY_INSTRUCTION_PREFIX`). Reranker:
  `BAAI/bge-reranker-v2-m3` (S3-11), a cross-encoder scoring `(query, chunk_text)` pairs directly.
- Every call traced via `traced_call` (S1-08); hybrid's prefilter/dense/bm25 legs and the
  cross-encoder's own rerank step are all separately traced sub-stages under the same query, per
  S3-09 through S3-11's design.
- Rungs 4-5's prefilter is populated from each question's own `nct_id` (`QueryFilters(nct_id=...)`),
  matching S3-10's "always on" design: this system answers questions about one trial at a time, so
  every real query already knows which trial it's scoped to.

## Results

| Rung | Retriever | Recall@1 | Recall@5 | Recall@10 | Recall@20 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|---|
| 1 | Dense only (`dense_search`) | 0.057 | 0.112 | 0.149 | 0.173 | 0.090 | 0.100 |
| 2 | Lexical only (`lexical_search`, "BM25") | 0.036 | 0.070 | 0.087 | 0.096 | 0.121 | 0.092 |
| 3 | Hybrid (RRF fusion, S3-09) | 0.059 | 0.158 | 0.214 | 0.254 | 0.149 | 0.151 |
| 4 | Hybrid + metadata prefilter (S3-10) | 0.175 | 0.376 | 0.467 | 0.619 | 0.412 | 0.383 |
| 5 | **+ cross-encoder rerank (S3-11)** | **0.288** | **0.570** | **0.619** | 0.619¹ | **0.606** | **0.557** |

¹ Rung 5's Recall@10 and Recall@20 are identical by construction, not coincidence: reranking narrows
every result list to top_k=8, so there is nothing to find at ranks 9-20 regardless of k.

Precision@k (for reference — expected to be low across the board since each question has only a
handful of true gold chunks out of ~30k candidates):

| Rung | Precision@1 | Precision@5 | Precision@10 | Precision@20 |
|---|---|---|---|---|
| Dense only | 0.060 | 0.024 | 0.016 | 0.010 |
| Lexical only | 0.096 | 0.060 | 0.044 | 0.026 |
| Hybrid (RRF) | 0.066 | 0.055 | 0.045 | 0.031 |
| Hybrid + prefilter | 0.306 | 0.182 | 0.130 | 0.099 |
| + cross-encoder rerank | **0.498** | **0.269** | **0.165** | 0.082² |

² Precision@20 drops for rung 5 relative to rung 4 only because the denominator (k=20) now
outnumbers the actual output length (top_k=8) — 12 of every 20 "slots" are necessarily empty and
scored as non-relevant, per `precision_at_k`'s documented convention (divides by `k` itself, not
`min(k, len(retrieved))`). Precision@1/5/10 are the honest comparison points for this rung.

## Reading the numbers

- **Hybrid beats both individual legs at every k** (e.g. Recall@10: 0.149 dense, 0.087 lexical,
  0.214 hybrid) — the expected, and here confirmed, benefit of RRF fusion: dense and lexical search
  surface different relevant chunks, and combining their rankings recovers more of the gold set than
  either alone. This is the core justification for building the hybrid rung at all.
- **Lexical has higher Precision@1 than dense** (0.096 vs. 0.060) despite lower recall — exact
  keyword/phrase matches (drug names, specific numbers, section headings) rank very precisely when
  they hit, but miss paraphrased or semantically-related content dense search would catch instead.
  This is the classic dense/lexical complementarity hybrid retrieval is built to exploit.
- **The metadata prefilter is the single biggest jump on the ladder so far**: Recall@10 more than
  doubles (0.214 → 0.467) and Recall@20 more than doubles as well (0.254 → 0.619, a 2.4x increase)
  once each query is restricted to the trial it's actually about, confirming the hypothesis noted
  in rung 3's analysis — most of
  rungs 1-3's recall ceiling was being spent distinguishing the right trial from the other 199,
  rather than finding the right chunk *within* the right trial. Precision@1 similarly jumps from
  0.066 to 0.306. This is strong evidence that S3-10's prefilter should stay "always on" in the
  final system, not an optional toggle, exactly as the sprint plan anticipated.
- **Absolute recall, while much improved, still has real headroom** (0.467 at k=10) — the remaining
  gap is presumably chunk-level ranking quality *within* the correct trial (order, chunk-type
  awareness, section boundaries), which is what S3-11's cross-encoder rerank targets next.
- **The cross-encoder rerank is the second-biggest jump on the ladder**: Recall@10 climbs another
  0.152 points (0.467 → 0.619, +33% relative) and Recall@1 more than doubles again (0.175 → 0.288),
  confirming the headroom hypothesis above — a cross-encoder scoring the actual `(query, chunk_text)`
  pair directly outranks bi-encoder cosine similarity for picking the single best passage out of a
  small, already-relevant candidate set. This is the expected trade a reranker makes: much better
  discrimination among a short list, at a real per-query cost (see Latency below) that only makes
  sense once the candidate set is already small and mostly relevant — which is exactly why it runs
  last in the ladder, after prefiltering has done the cheap work of narrowing the field.
- **The full five-rung ladder roughly quadruples Recall@10** relative to the naive dense-only
  baseline (0.149 → 0.619) and MRR by a similar margin (0.090 → 0.606) — each rung's improvement is
  real and additive, not redundant with the ones before it, which is the strongest evidence this
  ladder design (rather than jumping straight to the most sophisticated rung) was worth building
  incrementally and measuring at each step.

## Latency

Full per-stage latency (embed / dense / bm25 / fuse / prefilter / rerank / generate) for rungs 1-4
is deferred to S3-12's formal ablation report, which reads it directly from `retrieval_step.latency_ms`
grouped by `stage` — the rungs 1-4 draft above only ran one full pass to establish the ladder is
climbing correctly, not to characterize latency yet.

**Rung 5's rerank stage is the exception**: S3-11 specifically requires a real, evidence-based
latency number for the cross-encoder step alone (not the whole pipeline), read directly from the 301
`retrieval_step` rows where `stage = 'rerank'` for this run:

| Metric | Value |
|---|---|
| n | 301 |
| p50 | 9,413 ms |
| p95 | 36,005 ms |
| mean | 12,771 ms |
| min | 562 ms |
| max | 170,507 ms |

**This is a real, CPU-bound cost, not a footnote** — reranking 50 candidates against a 568M-parameter
cross-encoder (`BAAI/bge-reranker-v2-m3`) takes ~9.4 seconds at the median and can spike past 36
seconds at p95 on this hardware. The appendix's "reranking is cheap" framing does not hold on CPU at
this candidate-set size; it would need either a smaller/distilled cross-encoder, a GPU, or a reduced
`k_candidates` (currently 50) to be workable at interactive latency. The full 301-question rerank
pass took 3,860s (~64 minutes) wall-clock in this run — the first rung on this ladder where
per-query latency is a real deployment consideration, not an afterthought.
