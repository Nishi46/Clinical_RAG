# Sprint 3 — Implementation Breakdown

Atomic, ordered implementation steps for Sprint 3 (Retrieval Ladder + T1/T2 Eval / E3, E4, ~34h).
Companion to `sprint_plan.md`. Each step is small enough to check off individually.

**Status check before starting S3-01:** as of this writing, Sprint 2 is *not* fully closed — S2-01
through S2-05 are done (text extraction, naive chunker, OCR fallback, section segmentation, table
extraction), but **S2-06 (assessment-schedule reassembly), S2-07 (version tagging), S2-08
(section-aware chunker), S2-09 (formal `Chunk` schema), and S2-10 (ingestion report) are not yet
built.** S3-01 depends directly on S2-08's output (`data/chunks/{nct_id}/{doc_type}.jsonl`) and
S3-02's metadata columns depend on S2-09's schema. **Do not start S3-01 until every box on the
Sprint 2 exit checklist (`sprint_2_implementation.md`) is checked.** Everything below assumes that
checklist is closed and `data/chunks/` exists, is section-aware, never splits a table, and validates
against the S2-09 `Chunk` schema.

Two things this sprint reads from without re-deriving:
- Postgres `outcomes` table (`db/schema.sql`, populated by `db/extract.py::extract_outcomes`) already
  carries all three comparison sources — `registered_current`, `registered_first`, `results_reported`
  — keyed by `nct_id`/`kind`/`source`. T1 question generation (S3-03) reads facts from here, not from
  live registry snapshots.
- `configs/models.yaml` already pins `generation`/`judge` to `llama3.1:latest` (8B Q4,
  `sha256:46e0c1...`) and leaves `embeddings`/`reranker` unset — S3-01 and S3-11 are responsible for
  picking and pinning those two, following the same digest-pinning convention (Sprint 0's
  "reproducibility argument in the README" per `sprint_plan.md`'s appendix).

---

## S3-01 — Embedding pipeline (2h)

1. Pick a model and pin it: `BAAI/bge-base-en-v1.5` (768-dim, ~440MB, comfortable on 16GB, same
   `BAAI` family as the already-planned `bge-reranker-v2-m3` — keeps the retrieval stack internally
   consistent). Add `sentence-transformers>=3.0` to `pyproject.toml` under a new `retrieval` optional
   dependency group (not core — mirrors the `ocr` extra's pattern of keeping heavy/optional deps
   out of the default install).
2. Record the pick in `configs/models.yaml` under `embeddings:` — `name`, and a `revision` field
   holding the Hugging Face repo's pinned commit hash (`huggingface_hub.model_info(...).sha` or the
   revision shown on the model's HF "Files" tab), following the same "pin by digest, not tag"
   principle the generation/judge entries already use, since HF `main` can move.
3. Create `src/protocol_drift/retrieval/__init__.py` and `src/protocol_drift/retrieval/embed.py`.
   Implement `load_embedder(model_name, revision) -> SentenceTransformer`, loaded once and reused
   (model load is the expensive part; do not reload per batch).
4. Implement `embed_chunks(chunks: Iterable[Chunk], embedder, batch_size=32) -> Iterator[EmbeddedChunk]`
   — batches chunk `text` (the S2-08 contextual-header-prefixed text, so the header itself
   contributes to the embedding, per the plan's "contextual header" design) through
   `embedder.encode()`.
5. Cache by model digest: compute `cache_key = sha256(revision + chunk_id + text)`; skip
   re-embedding a chunk whose cache key already has a row (see S3-02's `chunks.embedding_cache_key`
   column) unless `--force` — this is the "cache everything, keyed on model digest" requirement from
   `sprint_plan.md`'s appendix, applied to the single most expensive one-time pass (embedding ~30k
   pages' worth of chunks).
6. Implement `embed_corpus(chunks_dir=Path("data/chunks"), ...) -> EmbeddingReport` iterating every
   `data/chunks/{nct_id}/{doc_type}.jsonl`, logging failures (empty text, encode errors) to
   `data/embeddings_errors.log` instead of aborting.
7. Run once across the full corpus as an overnight job (per the appendix's "one-time overnight job"
   estimate); confirm the run resumes near-instantly on a second invocation (cache hit on every row).
8. Write `tests/retrieval/test_embed.py`: a small fixture set of 3 chunks embeds to vectors of the
   expected dimension (768); re-running `embed_chunks` on the same chunks with the same revision
   produces zero new encode calls (mock/count the underlying `encode` call).

**Done when:** every chunk in `data/chunks/` has a cached embedding keyed on the pinned model
revision, and a second full run is a no-op.

---

## S3-02 — pgvector index + `tsvector` BM25 index + metadata columns (2h)

**Note on naming:** Postgres's built-in full-text search (`tsvector` + `ts_rank_cd`) is a
cover-density ranking function, **not** Okapi BM25 despite the common shorthand. Every place this
project's docs/tables say "BM25," it means "Postgres full-text search used as the lexical leg of
hybrid retrieval" — call this out explicitly in `results/ablation.md` (S3-12) so the results table
doesn't overclaim a ranking formula this stack doesn't actually run. If lexical recall in S3-12
looks weak, `ts_rank_cd`'s different weighting is a first suspect before blaming the fusion itself.

1. Create `src/protocol_drift/retrieval/schema.sql` (same one-schema.sql-per-package convention as
   `trace/schema.sql`, `db/schema.sql`). `CREATE EXTENSION IF NOT EXISTS vector;` then:
   ```sql
   CREATE TABLE IF NOT EXISTS chunks (
       chunk_id TEXT PRIMARY KEY,              -- "{nct_id}:{doc_type}:{chunk_index}"
       nct_id TEXT NOT NULL REFERENCES trials (nct_id),
       doc_type TEXT NOT NULL,
       doc_version INTEGER,
       section TEXT,
       subsection TEXT,
       page_range TEXT,
       chunk_type TEXT NOT NULL,               -- text / table / assessment_schedule
       is_ocr BOOLEAN NOT NULL DEFAULT FALSE,
       text TEXT NOT NULL,
       embedding vector(768),
       embedding_cache_key TEXT,
       text_search tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
   );
   ```
   The `chunk_id` format matches what `trace/schema.sql`'s `chunk_hit.chunk_id` already expects
   (TEXT, freeform) — pick it now so S3-05's retrieval scorer and S5-02's trace viewer read the same
   identifier without a join table.
2. Add `CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);` (pgvector ≥0.5 supports
   HNSW; fall back to `ivfflat` with `lists = 100` if the installed pgvector extension version is
   older — check `SELECT extversion FROM pg_extension WHERE extname='vector';` first).
3. Add `CREATE INDEX ON chunks USING gin (text_search);` for the lexical leg.
4. Add `CREATE INDEX ON chunks (nct_id, doc_type, doc_version);` for S3-10's prefilter.
5. Implement `src/protocol_drift/retrieval/load.py::load_chunks_into_db(chunks_dir, embeddings, conn)`
   — reads `data/chunks/**/*.jsonl` joined against S3-01's cached embeddings by `chunk_id`, bulk
   upserts into `chunks` (`ON CONFLICT (chunk_id) DO UPDATE` so a re-run after a chunker fix doesn't
   duplicate rows).
6. Run against the full corpus; `SELECT count(*) FROM chunks` should match the total chunk count
   across `data/chunks/`.
7. Sanity query: `SELECT count(*) FROM chunks WHERE embedding IS NULL` must be zero — a chunk with no
   embedding would silently vanish from every dense-retrieval rung without erroring.
8. Write `tests/retrieval/test_schema.py` (marked `db`, per the existing `db`/`integration` marker
   convention): insert one chunk, confirm the HNSW/ivfflat index is used by `EXPLAIN` on a
   `ORDER BY embedding <=> ...` query, and that `text_search` populates automatically from `text`.

**Done when:** `chunks` is populated for the full corpus with dense, lexical, and metadata indexes
all live, and zero rows are missing an embedding.

---

## S3-03 — T1 question generation (4h)

Auto-generated from the registry facts already sitting in Postgres — the resulting eval set is only
as good as the gold-chunk-ID location step, which is the part that can't be templated away.

1. Create `src/protocol_drift/eval/__init__.py` and `src/protocol_drift/eval/t1_questions.py`.
   Define ~6-8 question templates over fields confirmed extractable per `field_paths.md` and already
   in `trials`/`outcomes`/`arms`/`eligibility`: enrollment target, primary outcome measure + timeframe
   (`source='registered_current'`), phase, sponsor, min/max age, arm labels. E.g. `"What is the
   target enrollment for {nct_id}?"` → gold answer from `trials` (note: enrollment count isn't yet a
   column in `trials` per `db/schema.sql` — add `enrollment_count INTEGER` via a migration in this
   task, extracted from `protocolSection.designModule.enrollmentInfo.count` in the archived snapshot,
   since T1 needs it and it isn't there yet).
2. Implement `generate_t1_questions(cohort, conn) -> list[T1Question]` — for each cohort trial, fill
   templates from its Postgres row(s), skip a template if the underlying field is null/empty rather
   than emitting a garbage question.
3. Implement `locate_gold_chunk(nct_id, doc_type, answer_text, conn) -> list[str]` — normalized
   substring/fuzzy search (e.g. `rapidfuzz` token-set ratio, threshold ~85) of `answer_text` against
   `chunks.text` for that `nct_id`, restricted to `doc_type='protocol'` first, falling back to `sap`;
   return every matching `chunk_id` (a fact can legitimately appear in more than one chunk — record
   all of them, not just the first hit, so Recall@k isn't penalized for finding a valid second
   citation).
4. Log questions whose answer can't be located in any chunk (expected for facts that exist in the
   registry but were never restated in the PDF, e.g. some enrollment targets) to
   `data/eval/t1_unlocatable.log` and exclude them from the frozen set — an ungrounded gold ID is
   worse than a smaller eval set.
5. Cap and stratify: target ~200 questions per `project_plan.md` §7.1, capped per-trial (e.g. ≤2 per
   template per trial) so a handful of data-rich trials don't dominate the set.
6. Write `data/eval/t1.jsonl`: `question_id`, `nct_id`, `question_text`, `gold_answer`,
   `gold_chunk_ids[]`, `template_id`.
7. Write `tests/eval/test_t1_questions.py`: a fixture trial with known Postgres rows and known chunk
   text produces the expected question text and the expected gold chunk ID via `locate_gold_chunk`.

**Done when:** `data/eval/t1.jsonl` has ~200 questions, every one has ≥1 gold chunk ID located by
substring/fuzzy match (not asserted by hand), and unlocatable candidates are logged, not silently
dropped.

---

## S3-04 — T2 question set (4h)

~100 hand-written protocol-only questions — this is manual labeling time, budget it as such, not as
coding time.

1. Sample documents to write against: stratify by the same sponsor-class × phase buckets as the
   cohort (`docs/corpus.md`'s stratification table) so T2 isn't accidentally concentrated on 3
   sponsors' protocols.
2. Write questions whose answer requires reading protocol/SAP prose that has **no** registry-field
   equivalent (dose-modification rules, specific eligibility exclusions with clinical reasoning,
   assessment-schedule procedure details) — the `project_plan.md` §7.1 example ("dose-modification
   rules for grade 3 neutropenia") is the right difficulty level.
3. For each question, hand-identify the gold `chunk_id`(s) by locating the answer in
   `data/chunks/{nct_id}/{doc_type}.jsonl` directly (same identifier format as S3-02's `chunk_id`
   column) — this is the one part of T2 that can't be automated; do it while the source page is open
   in front of you.
4. Include a mix of `chunk_type` targets: at least ~15 questions whose gold answer lives specifically
   in an `assessment_schedule`-type chunk, since that's the structure the naive-vs-section-aware
   comparison (S2-10) exists to showcase and T2 should be able to measure whether retrieval actually
   benefits from it.
5. Write `data/eval/t2.jsonl`: same shape as `t1.jsonl` minus `template_id`, plus a free-text
   `gold_answer_notes` field for the human answer key (used by S3-08's judge calibration, not by
   exact-match scoring).
6. Write `tests/eval/test_eval_schema.py`: both `t1.jsonl` and `t2.jsonl` validate against a shared
   `pydantic` `EvalQuestion` model (fail loudly on a malformed hand-entered row rather than surfacing
   as a mysterious scorer bug later).

**Done when:** `data/eval/t2.jsonl` has ~100 hand-written questions with hand-verified gold chunk
IDs, spanning multiple sponsors and at least one `assessment_schedule`-targeted subset.

---

## S3-05 — Retrieval scorer (2h)

1. Create `src/protocol_drift/eval/retrieval_scorer.py`. Implement
   `recall_at_k(retrieved_chunk_ids, gold_chunk_ids, k) -> float`,
   `precision_at_k(...)`, `mrr(...)`, `ndcg_at_k(retrieved, gold, k=10)` — plain-Python
   implementations (no new dependency; this is small enough not to justify `pytrec_eval`).
2. Implement `score_retrieval_run(questions, retrieve_fn, ks=(1,5,10,20)) -> RetrievalScores` —
   runs `retrieve_fn(question.question_text) -> list[chunk_id]` per question, scores against
   `gold_chunk_ids`, aggregates mean Recall@k/Precision@k/MRR/nDCG@10 across the question set.
3. Every call inside `score_retrieval_run` must go through `traced_call` (S1-08) so per-stage latency
   and chunk hits land in the trace store automatically — this is what makes S3-12's ablation table
   regenerate from traces instead of being hand-typed.
4. Write `tests/eval/test_retrieval_scorer.py`: a hand-constructed retrieved-list/gold-list pair
   produces hand-computed Recall@1/5, MRR, and nDCG@10 values (verify the nDCG log-discount formula
   against a worked example, not just "it runs").

**Done when:** `score_retrieval_run` produces all four metric families from a `retrieve_fn`, and
every question scored writes a full trace.

---

## S3-06 — Answer generation (2.5h)

1. Create `src/protocol_drift/generation/__init__.py` and
   `src/protocol_drift/generation/ollama_client.py` — a thin wrapper over Ollama's local REST API
   (`http://localhost:11434/api/generate`) using the existing `requests` dependency directly (per
   `project_plan.md` §11's "Plain Python" stack choice — no new LLM client library). Implement
   `generate(prompt, model=<configs/models.yaml generation.name>, digest=<pinned digest>) -> str`,
   verifying the locally-resolved digest matches the pinned one before every run (raise if
   `ollama show <name>` reports a different digest — catches silent tag drift per the appendix's
   "pin by digest, not tag" guarantee).
2. Create `src/protocol_drift/generation/answer.py`. Implement `build_prompt(question, chunks) -> str`
   — numbered chunk list, each prefixed with its S2-08 contextual header (so the model sees
   `nct_id`/`doc_type`/`section` inline), instruction to cite chunk numbers inline, and an explicit
   instruction to answer `"NOT_ANSWERABLE"` verbatim if the retrieved chunks don't contain the
   answer (the refusal path S4-09/S5 need later).
3. Implement `generate_answer(question, retrieved_chunks, store: TraceStore) -> GeneratedAnswer` —
   calls `ollama_client.generate`, computes `prompt_hash` via `compute_prompt_hash` (already defined
   in `trace/store.py`), logs via `store.log_generation` (query_id, model_digest, prompt_hash,
   response_text, latency_ms, token_count) and `store.log_cost`.
4. Cache on `prompt_hash`: before calling Ollama, check the trace store for an existing `generation`
   row with the same `prompt_hash` + `model_digest` and reuse its `response_text` if found — this is
   the caching contract `store.py`'s docstring flagged as needed starting now.
5. Parse citations out of `response_text` (chunk numbers referenced) into `GeneratedAnswer.cited_chunk_ids`
   — needed by S3-07's faithfulness scorer.
6. Write `tests/generation/test_answer.py` with the Ollama HTTP call mocked: `build_prompt` includes
   every chunk's contextual header; a mocked `NOT_ANSWERABLE` response round-trips as a refusal, not
   a parsed citation list.

**Done when:** a question + retrieved chunks produces a cited answer end-to-end, every call is
traced, and re-running the identical prompt hits the cache instead of calling Ollama again.

---

## S3-07 — Correctness + faithfulness scorers (3h)

1. Create `src/protocol_drift/eval/correctness_scorer.py`. Implement
   `exact_match_score(generated, gold) -> bool` (normalized: lowercase, strip units/punctuation) for
   T1 — reuse the same normalization helper S4-03 will later extend for outcome phrasing, factored
   into `src/protocol_drift/normalize/text.py` now so it isn't duplicated in Sprint 4.
2. Implement `judged_correctness(question, generated_answer, gold_notes, ollama_client) -> float` for
   T2 — prompts the judge model (per `configs/models.yaml`'s `judge` entry) with the question, the
   generated answer, and the hand-written `gold_answer_notes`, asking for a 0/0.5/1 correctness score
   plus one-sentence justification; parse the score defensively (retry once on unparseable output,
   then log and treat as `None` rather than crashing a full eval run).
3. Implement atomic-claim faithfulness: `extract_claims(generated_answer_text) -> list[str]` (prompt
   the judge to split the answer into atomic factual claims), then
   `claim_grounded(claim, retrieved_chunks, judge) -> bool` per claim — `faithfulness_score = grounded
   claims / total claims`. This is a second judge call per answer; note the multiplier in `docs/`
   (roughly the "~6 calls per eval question" the appendix already budgets for).
4. Wire both scorers through `traced_call`/`log_generation` the same way S3-06 does, so judge calls
   are themselves traced and cached on `prompt_hash` — a judge re-run over unchanged answers should
   also cost zero compute.
5. Write `tests/eval/test_correctness_scorer.py`: `exact_match_score` handles a known unit-format
   variant ("24 months" vs "2 years" — deliberately the same example `discrepancy_definition.md` uses
   for S4-03, since T1's timeframe questions will hit this immediately); the judge-scoring path is
   tested with the Ollama call mocked to return a canned score.

**Done when:** T1 questions score via exact/normalized match with no model call, T2 questions score
via the judge with justification text logged, and faithfulness produces a per-answer grounded-claim
ratio.

---

## S3-08 — Judge calibration (3h)

1. From `data/eval/t2.jsonl`, sample 50 questions; run S3-06's `generate_answer` against them (rung
   whatever retrieval config is currently wired — this need not wait for the full ladder).
2. Hand-label each of the 50 responses yourself against a fixed 0/0.5/1 rubric (write the rubric
   first, as a short doc — reuse the rubric-first discipline `discrepancy_definition.md` establishes
   for S4-07, applied here a sprint earlier).
3. Run S3-07's `judged_correctness` on the same 50 responses.
4. Implement `cohens_kappa(human_labels, judge_labels) -> float` in
   `src/protocol_drift/eval/calibration.py` — either via `scikit-learn`'s `cohen_kappa_score` (add
   `scikit-learn` to a new `eval` optional dependency group) or a direct implementation; either way,
   round 0.5 scores to a small fixed label set (`{0, 0.5, 1}`) before computing agreement, and use a
   weighted kappa (linear weights) since the labels are ordinal, not nominal.
5. Write `docs/judge_calibration.md`: κ value, the confusion matrix (human vs. judge across the 3
   labels), and the 50 rated examples with both scores side by side.
6. If κ < 0.6: read the disagreements, revise the judge prompt/rubric (tighter definition of what
   counts as "0.5 — partially correct" is the most likely fix), re-run the same 50, recompute κ.
   Iterate at most twice before accepting the current κ and reporting it honestly — this task has a
   3h budget, not an unbounded one.
7. Write `tests/eval/test_calibration.py`: `cohens_kappa` against a hand-computed toy confusion
   matrix with a known kappa value.

**Done when:** `docs/judge_calibration.md` reports a concrete κ from 50 human-vs-judge labels on real
T2 responses, with the rubric and any revision history documented.

---

## S3-09 — Ladder rung 3: BM25 + RRF fusion (2h)

1. Create `src/protocol_drift/retrieval/lexical.py`. Implement `lexical_search(query, k, conn) ->
   list[(chunk_id, score)]` — `SELECT chunk_id, ts_rank_cd(text_search, query) AS score FROM chunks,
   plainto_tsquery('english', %s) query WHERE text_search @@ query ORDER BY score DESC LIMIT %s`.
2. Create `src/protocol_drift/retrieval/dense.py`. Implement `dense_search(query_embedding, k, conn)
   -> list[(chunk_id, score)]` — `ORDER BY embedding <=> %s LIMIT %s` (cosine distance, matching the
   `vector_cosine_ops` index from S3-02).
3. Create `src/protocol_drift/retrieval/fuse.py`. Implement `reciprocal_rank_fusion(rankings:
   list[list[chunk_id]], k=60) -> list[(chunk_id, float)]` — standard RRF: `score(d) = sum over
   rankings containing d of 1 / (k + rank(d))`, sorted descending.
4. Implement `hybrid_search(query, k, embedder, conn) -> list[chunk_id]` — runs `dense_search` and
   `lexical_search` in parallel (or sequentially; not enough volume here to need real concurrency),
   fuses via `reciprocal_rank_fusion`, each stage wrapped in `traced_call(store, query_id, "dense")` /
   `"bm25"` / `"rrf"` per the trace schema's existing `stage` CHECK constraint.
5. Re-run S3-05's `score_retrieval_run` with `retrieve_fn=hybrid_search` over T1+T2; append the row
   to `results/ablation.md` (draft table — S3-12 formalizes the full auto-generation).
6. Write `tests/retrieval/test_fuse.py`: a hand-worked 2-ranking RRF example produces the exact
   expected fused order and scores (k=60, small integer ranks — arithmetic checkable by hand).

**Done when:** `hybrid_search` runs end-to-end and traced, and its Recall@10 is logged next to rung
1-2's numbers.

---

## S3-10 — Ladder rung 4: metadata prefilter (2h)

1. Create `src/protocol_drift/retrieval/query_parse.py`. Implement `parse_query_filters(query_text) ->
   QueryFilters` — regex `NCT\d{8}` for an explicit NCT ID, keyword match (`"protocol"`, `"SAP"`,
   `"statistical analysis plan"`) for `doc_type`, and a version/amendment pattern (`"amendment N"`,
   `"version N"`) for `doc_version` — all optional; most T1/T2 questions won't carry an NCT ID inline
   since they're often asked in a single-trial context passed separately, so also accept an explicit
   `nct_id` parameter from the caller (the eval harness always knows which trial a question targets)
   as a filter source in addition to text parsing.
2. Extend `hybrid_search` (S3-09) to `hybrid_search(query, k, embedder, conn, filters:
   QueryFilters | None)` — when filters are present, add a `WHERE nct_id = %s AND doc_type = %s ...`
   clause to both `dense_search` and `lexical_search` before ranking, wrapped in its own
   `traced_call(..., "prefilter")` stage that logs the filter applied (even though prefilter itself
   returns no chunk hits — log latency + a note of which filters fired, useful for S5's failure
   analysis on `X-PARTIAL`/`E-ARM` cases later).
3. Re-run the eval with filters always populated from each question's known `nct_id` (the realistic
   case: this system answers questions about one trial at a time, so the prefilter should be closer
   to "always on" than optional) — append to `results/ablation.md`.
4. Write `tests/retrieval/test_query_parse.py`: a query containing an explicit NCT ID and "SAP"
   parses both filters correctly; a query with neither returns an all-`None` `QueryFilters` without
   raising.

**Done when:** prefiltered hybrid search is traced and scored, and its Recall@10 sits next to rung
3's number in the ablation draft.

---

## S3-11 — Ladder rung 5: cross-encoder rerank (2.5h)

1. Pin the reranker in `configs/models.yaml`: `BAAI/bge-reranker-v2-m3` (already named in
   `sprint_plan.md`/`project_plan.md`; not yet pulled per the config's own comment) — record `name`
   and HF `revision` the same way S3-01 did for the embedder.
2. Create `src/protocol_drift/retrieval/rerank.py`. Implement `load_reranker(model_name, revision) ->
   CrossEncoder` (`sentence_transformers.CrossEncoder`, already covered by the `retrieval` optional
   dependency group added in S3-01 — no new dependency needed).
3. Implement `rerank(query, candidate_chunks: list[Chunk], top_k=8) -> list[chunk_id]` — scores every
   `(query, chunk.text)` pair via `reranker.predict`, sorts descending, returns the top 8.
4. Implement `rerank_ladder(query, k_candidates, embedder, conn, filters) -> list[chunk_id]` —
   retrieves 50 candidates via S3-10's prefiltered `hybrid_search`, reranks to top 8, wrapped in
   `traced_call(..., "rerank")`.
5. Re-run the eval with `retrieve_fn=rerank_ladder`; append to `results/ablation.md`.
6. Note the latency cost explicitly in the same table — reranking 50 candidates per query is the
   first rung where per-query latency becomes visible on CPU; record p50/p95 for this stage alone so
   S3-12's stage breakdown has a real number to show, not just "reranking is cheap" from the appendix
   restated without evidence.
7. Write `tests/retrieval/test_rerank.py` with the cross-encoder mocked to a fixed scoring function:
   `rerank` returns exactly `top_k` results in the mocked score's descending order.

**Done when:** rerank-topped retrieval is traced, scored, and its per-stage latency is recorded
alongside its Recall@10.

---

## S3-12 — Ablation runs (3h + wall clock)

1. Create `src/protocol_drift/eval/ablation.py`. Implement `run_rung(name, retrieve_fn, questions,
   generate_answers=True) -> RungResult` — runs S3-05's retrieval scoring and, if
   `generate_answers`, S3-06/S3-07's generation + correctness + faithfulness scoring, all the way
   through T1+T2, entirely through traced calls.
2. Implement `run_ablation(rungs: list[(name, retrieve_fn)], questions) -> AblationReport` — runs all
   five rungs in order (fixed-chunk dense-only baseline rung 1 is effectively S3-01+S3-02's
   `dense_search` alone; rungs 2-5 are S3-09 through S3-11's functions plus the naive-baseline
   dense-only floor) sequentially, one full pass each.
3. Implement `render_ablation_md(report) -> str` reading directly from the trace store (per-rung
   Recall@{1,5,10,20}/Precision@k/MRR/nDCG@10 aggregated by `SELECT ... FROM retrieval_step JOIN
   chunk_hit ...`, latency broken out per `stage`) — **no hand-copied numbers**, matching the
   acceptance criterion verbatim. Write to `results/ablation.md`.
4. Add a `scripts/run_ablation.py` entrypoint and a `make ablation` target (same "regenerates via
   `scripts/`" pattern as `corpus-report`/`ingestion-report`).
5. Run the full 5-rung sweep overnight (per the appendix's "roughly two overnight runs" estimate for
   T1+T2 volume at ~6 calls/question); confirm cache hits make a second immediate re-run near-instant.
6. Spot-check the rendered table against 2-3 manually-verified rows before trusting it as the
   headline number.
7. Write `tests/eval/test_ablation.py`: `render_ablation_md` against a small fixture set of trace-DB
   rows produces the expected table cells (verifies the SQL aggregation, not a live model run).

**Done when:** `results/ablation.md` exists, regenerates from `make ablation` with zero hand-typed
numbers, and shows a monotonic (or explainably non-monotonic) trend across the five rungs.

---

## Sprint 3 acceptance criteria

- Every T1 question has a gold chunk ID; retrieval recall is computable
- `results/ablation.md` regenerates from traces with one command — no hand-copied numbers, ever
- Latency broken out per stage (embed / dense / BM25 / fuse / prefilter / rerank / generate)
- κ reported in `docs/judge_calibration.md`

---

## 🚧 GATE S3-G1 — Local model adequacy

Run **after S3-07**, before the full S3-12 ablation sweep — discovering this after the sweep wastes
several nights of local compute.

1. Take T1's ~200 questions; feed each question's **gold chunks directly** to S3-06's
   `generate_answer` (skip retrieval entirely — this isolates generation quality from retrieval
   quality).
2. Score with S3-07's `exact_match_score`.
3. Record the result in `docs/judge_calibration.md` alongside the κ number, or a short standalone
   `docs/model_gate.md`.

| Result | Action |
|---|---|
| **≥ 80%** correctness on perfect retrieval | Local `llama3.1:latest` 8B is adequate for T1/T2. Proceed to S3-12 as specced. |
| **< 80%** | The model, not retrieval, is the ceiling. Per `sprint_plan.md`'s appendix escape hatch: route *generation only* for T2/T3 to Google AI Studio (~1,500 req/day, no card, ~250 questions/day at ~6 calls/question), keep retrieval/reranking/T1/T4 local, and document the split explicitly in `results/ablation.md` and later the README — a measured hybrid choice reads as engineering judgment, not compromise. |

---

## Sprint 3 exit checklist

- [ ] `chunks` table populated (embeddings, tsvector, metadata) for the full corpus; zero rows
      missing an embedding
- [ ] `data/eval/t1.jsonl` — ~200 questions, every gold chunk ID located programmatically
- [ ] `data/eval/t2.jsonl` — ~100 hand-written questions, hand-verified gold chunk IDs
- [ ] `docs/judge_calibration.md` — κ reported from 50 human-vs-judge labels
- [ ] Gate S3-G1 recorded — local-model adequacy result and any escape-hatch decision documented
- [ ] `results/ablation.md` — regenerates via `make ablation`, all 5 rungs, latency per stage
- [ ] `configs/models.yaml` — `embeddings` and `reranker` entries pinned by revision, no longer null
- [ ] CI still green: ruff + mypy + pytest (integration/db tests excluded) on every push

Do not start Sprint 4's cross-source work (S4-01) until every box above is checked and gate S3-G1's
outcome (proceed locally, or hybrid free-tier split) is recorded — S4-05's discrepancy detector
inherits whichever generation path this gate settles on.
