# Sprint 5 — Implementation Breakdown

Atomic, ordered implementation steps for Sprint 5 (Serving & Failure Analysis / E6, ~21h). Companion
to `sprint_plan.md`. **Failure analysis is the deliverable here — everything before this sprint was
setup for it.** Per the milestone table: *"My top failure category was X; I fixed it and here's the
measured delta"* is the sentence Sprint 5 exists to earn.

**Do not start S5-01 until every box on the Sprint 4 exit checklist is checked.** S5-05's failure
labeling and S5-06's Pareto chart draw directly on Sprint 3's `results/ablation.md` and Sprint 4's
`docs/discrepancy_eval.md` as raw material — they are not new eval runs, they are a re-read of
existing trace-store data through a different lens.

The failure taxonomy is already fixed by `project_plan.md` §9 — use it verbatim, do not invent new
codes mid-sprint:

| Code | Failure | Typical fix |
|---|---|---|
| `R-MISS` | Gold chunk never retrieved | Chunking, embeddings, query rewrite |
| `R-DISTRACT` | Outranked by near-duplicate from another trial or arm | Metadata filter, reranking |
| `T-MANGLE` | Assessment-schedule table destroyed; value read from wrong row/column | Table serialization |
| `T-SCAN` | Content in a scanned page never extracted | OCR fallback, page classification |
| `V-AMEND` | Answer pulled from a superseded protocol version | Amendment tagging |
| `E-ARM` | Confused study arms or cohorts | Arm-aware metadata |
| `X-PARTIAL` | Found 2 of 3 sources needed for a comparison | Multi-hop retrieval |
| `X-FALSEPOS` | Flagged a discrepancy that isn't one | Comparison prompt, normalization |
| `G-HALLUC` | Claim absent from retrieved context | Grounding constraints |
| `G-UNIT` | Right value, wrong unit or timeframe | Header propagation |
| `A-OVERREFUSE` | Refused an answerable question | Prompt calibration |

---

## S5-01 — FastAPI + SSE streaming answer endpoint (2.5h)

1. Add `fastapi` and `uvicorn` (and `sse-starlette`, or hand-roll SSE via
   `StreamingResponse`/`text/event-stream` — hand-rolling is a handful of lines and keeps the "Plain
   Python" stack promise from `project_plan.md` §11) to a new `serving` optional dependency group in
   `pyproject.toml`.
2. Create `src/protocol_drift/serving/__init__.py` and `src/protocol_drift/serving/app.py`.
   Implement `POST /answer` accepting `{nct_id, question, tier?}`, running S3-11's
   `rerank_ladder` (or S4-04's `answer_cross_source_query` when `tier == "T3"`) followed by S3-06's
   `generate_answer`, streaming tokens as they arrive from Ollama's own streaming response
   (`stream=True` on the `/api/generate` call in `ollama_client.py` — extend it with a
   `generate_stream` variant rather than replacing the cached non-streaming path S3-06 already
   depends on).
3. Every request still logs through `TraceStore`/`traced_call` exactly as the eval harness does —
   the serving path and the eval path share the same generation/retrieval functions, not
   parallel reimplementations, so a query answered through the API produces the same trace-store
   rows a batch eval run would.
4. Add `GET /health` (checks a live Postgres connection) and `GET /discrepancy/{nct_id}` returning
   S4-05's `DiscrepancyReport` for a trial (used by S5-03).
5. Write `tests/serving/test_app.py` using FastAPI's `TestClient`: `/answer` streams a mocked
   response end-to-end; `/health` reflects a real or mocked DB connection state; `/discrepancy/{nct_id}`
   404s cleanly for an out-of-cohort NCT ID rather than raising.

**Done when:** `/answer` streams a real cited response for an in-cohort question, and every request
produces the same trace-store rows a direct function call would.

---

## S5-02 — Trace viewer (3.5h)

The single UI artifact both the README (per S6-01) and this sprint's failure-labeling workflow
(S5-05) depend on — build it functional before polishing it.

1. Create `src/protocol_drift/serving/trace_viewer.py` — a server-rendered HTML page (Jinja2 template,
   or plain f-string templating consistent with the corpus/ingestion report scripts' "string
   templating is enough" precedent) at `GET /trace/{query_id}`.
2. Render, per query: the question text and tier; retrieved chunks with dense score, BM25
   (`ts_rank_cd`) score, RRF-fused score, and rerank score **side by side** (pull from `chunk_hit`
   rows across all `retrieval_step`s for that `query_id` — this is exactly the "render a query's full
   retrieval trace from this table alone" design already noted in `trace/schema.sql`'s comments);
   the generated answer with inline citation highlighting; per-stage latency (`retrieval_step.stage`
   → `latency_ms`, plus `generation.latency_ms`).
3. Add a source-page link: `nct_id`/`doc_type`/`page_range` from `chunk_hit` resolves to a link that
   opens the source PDF at the right page (`data/pdfs/{nct_id}/{nct_id}_{doc_type}.pdf#page=N` — most
   browsers honor the `#page=` fragment on a local/served PDF).
4. Add `GET /trace/recent` listing the last N queries with a one-line summary each, linking into the
   per-query view — the entry point for browsing during failure labeling (S5-05) without needing to
   know query IDs in advance.
5. Write `tests/serving/test_trace_viewer.py`: given a fixture set of trace-store rows for one
   `query_id`, the rendered page contains every expected chunk's `nct_id`, all four score columns,
   and the correct source-page link.

**Done when:** a real query's full trace — chunks, all score types, latency, source links — renders
correctly on one page, and this page is good enough to screenshot for the README (S6-01 reuses it
directly).

---

## S5-03 — Discrepancy report view (2.5h, 🟡 — JSON is an acceptable substitute)

Fifth on the cut list ("JSON output is enough") — if the sprint is tight, ship `GET
/discrepancy/{nct_id}` from S5-01 as the deliverable and skip the HTML rendering below; note the cut
explicitly in the sprint retro rather than silently dropping it.

1. Create `src/protocol_drift/serving/discrepancy_view.py` — `GET /discrepancy/{nct_id}/view`,
   rendering S4-05's `DiscrepancyReport`: the three pairwise verdicts side by side (first-posted vs.
   current, current vs. protocol, registry vs. results), each with its verdict label, one-sentence
   structural description (reusing S4-05's `render_verdict_text`, so the ethics-language guardrail
   test already covers this view's output too), and citations linking to the specific `outcomes` row
   or protocol chunk/page involved.
2. Visually distinguish `match` / `divergence` / `ambiguous` / `retrieval_failed` (four states, not
   three — per S4-05, retrieval failure is tracked separately from a normalization `ambiguous`
   verdict, and the UI should not blur that distinction either).
3. Link each protocol-side citation into S5-02's trace viewer / source-page link, so a viewer can go
   from "divergence flagged" straight to the actual retrieved text.
4. Write `tests/serving/test_discrepancy_view.py`: a fixture `DiscrepancyReport` with one `divergence`
   and one `retrieval_failed` pair renders both distinctly, not collapsed into the same visual state.

**Done when (if not cut):** the three-source comparison renders with citations resolving to real
source locations, and the four verdict states are visually distinct.

---

## S5-04 — Deploy to a public URL (1.5h, 🟡)

1. Pick a free-tier host consistent with the $0 budget (`sprint_plan.md`'s planning assumptions) —
   e.g. Railway/Render/Fly.io free tier for the FastAPI app; note that Ollama itself likely can't run
   on a free tier's resource limits, so the deployed instance either (a) serves pre-computed/cached
   answers only (every `generation` row is already cached by `prompt_hash` per S3-06 — a deployed
   read-only mode that only serves cached traces is a legitimate, honestly-labeled scope cut), or
   (b) proxies generation calls back to a local Ollama instance over a tunnel, which is more fragile.
   Decide explicitly and document the choice in `docs/deployment.md` rather than letting the demo
   silently degrade.
2. Add environment-based config (`DATABASE_URL`, `OLLAMA_HOST`) via `python-dotenv` (already a
   dependency) so the same `app.py` runs locally and deployed without code changes.
3. Deploy; confirm `/health` and at least one full `/answer` + `/trace/{query_id}` round-trip work
   against the deployed instance.
4. Write `docs/deployment.md`: host, URL, the cached-vs-live generation decision from step 1, and
   redeploy instructions.

**Done when (if not cut):** a public URL serves `/health`, `/answer`, and the trace viewer, with the
generation-mode tradeoff explicitly documented.

---

## S5-05 — Label 100 failures using the taxonomy (4h)

The core of the sprint. Pulls from Sprint 3's ablation runs and Sprint 4's discrepancy eval — not a
new eval pass.

1. Create `src/protocol_drift/analysis/__init__.py` and `src/protocol_drift/analysis/failures.py`.
   Implement `collect_failure_candidates(conn) -> list[FailureCandidate]` — pulls every T1/T2 question
   scored below a correctness threshold from S3-12's ablation runs (use the best/final rung, not rung
   1, since that's what the system actually ships), every T3/T4 question with a wrong answer, and
   every discrepancy verdict from S4-08 that disagreed with the adjudicated label.
2. Stratify and sample 100 across tiers (T1/T2/T3/T4/discrepancy) proportionally to where failures
   actually concentrate, not uniformly — a tier with a 5% error rate and a tier with a 40% error rate
   shouldn't get equal sample counts if the goal is finding the dominant failure mode.
3. Build a lightweight labeling flow: for each sampled failure, surface the question, the generated
   answer, the retrieved chunks (via S5-02's trace viewer — this is the workflow reason the trace
   viewer had to exist first), and the gold answer/label side by side; assign exactly one primary
   taxonomy code from the table above per failure (per `project_plan.md` §9: "one primary label
   each" — resist assigning two, it breaks the Pareto count).
4. Write `data/analysis/failure_labels.jsonl`: `question_id` (or discrepancy trial ID), `tier`,
   `code`, one-sentence rationale.
5. Write `tests/analysis/test_failures.py`: `collect_failure_candidates` correctly identifies a
   fixture question scored below threshold and excludes one scored above it.

**Done when:** `data/analysis/failure_labels.jsonl` has exactly 100 labeled failures, stratified
across tiers, each with one primary taxonomy code.

---

## S5-06 — Pareto chart of failure categories (1h)

1. Create `src/protocol_drift/analysis/pareto.py`. Implement `pareto_counts(failure_labels) ->
   list[(code, count, cumulative_pct)]` sorted descending by count.
2. Render as a chart — matplotlib is the pragmatic choice here (add to a new `analysis` optional
   dependency group) since this is a one-off static image for `docs/failure_analysis.md`, not a live
   dashboard; bar chart of counts with a cumulative-percentage line, standard Pareto form.
3. Save to `docs/assets/failure_pareto.png`; also emit the raw counts table as Markdown for the
   report (image plus table, not image alone — a table is what actually regenerates cleanly and is
   diffable in review).
4. Write `tests/analysis/test_pareto.py`: a small fixture label set produces the expected sorted
   counts and cumulative percentages.

**Done when:** `docs/assets/failure_pareto.png` and its underlying counts table both exist and
regenerate from `data/analysis/failure_labels.jsonl`.

---

## S5-07 — Fix top 2 categories, re-run, report delta (4h + wall clock)

1. From S5-06's Pareto output, pick the top 2 categories by count. Read their `typical fix` column
   from the taxonomy table as a starting hypothesis, then look at the actual labeled examples in
   `data/analysis/failure_labels.jsonl` for that code — the fix should target the specific failures
   observed, not the generic taxonomy suggestion blindly.
2. Implement each fix as a small, targeted change to the relevant existing module (e.g. `T-MANGLE` →
   revisit `assessment_schedule.py`'s continuation-table detection; `R-DISTRACT` → tighten S3-10's
   metadata prefilter defaults; `G-UNIT` → extend S4-03's `normalize_timeframe` unit coverage) — do
   not build new infrastructure for this; the point is a measured intervention on the existing
   pipeline, not a rewrite.
3. Re-run the affected slice of the eval (the specific tier(s) each fix touches, not necessarily the
   full S3-12 sweep, to keep this inside a 4h+wall budget) via the existing `make ablation` /
   discrepancy-scorer entrypoints — this reuses S3-12/S4-08's machinery unchanged.
4. Compute the per-category delta: what fraction of the top-2 categories' *specific labeled failures*
   are now fixed, plus the overall metric movement (Recall@10, correctness, discrepancy F1 —
   whichever the fix targets) before/after.
5. Write `docs/failure_analysis.md`: the Pareto chart + table from S5-06, the two interventions
   described concretely (what changed, in which file), and a before/after table **per category** —
   report honestly even if a delta is small or a fix only partially worked; a documented partial fix
   is more credible than a hidden one.
6. Write a same-day retro note in `docs/retros/` per the project's Definition of Done.
7. Add regression tests for each fix in its module's existing test file (not a new file) — the fix
   should be permanently enforced, not just manually verified once.

**Done when:** `docs/failure_analysis.md` states, per category, exactly what changed and the measured
before/after delta — including an honestly small one.

---

## S5-08 — Dedicated X-FALSEPOS analysis (2h, 🟡 cuttable)

Second on the cut list — attempt only if S5-01 through S5-07 are done with time remaining.

1. Filter `data/analysis/failure_labels.jsonl` (plus, if n=100 turned up few `X-FALSEPOS` cases,
   supplement directly from S4-08's discrepancy scoring — every detector `divergence` verdict the
   adjudicated label called `match`) to the `X-FALSEPOS` subset specifically.
2. For each, trace back through S4-03's `compare_outcomes`: was it a construct-normalization miss
   (judge misclassified genuinely equivalent phrasing) or a timeframe/unit miss (should have hit the
   deterministic path in `normalize_timeframe` but didn't — e.g. an unhandled unit format)?
3. Write `docs/false_positive_analysis.md`: the breakdown above, 2-3 concrete examples with the exact
   phrasing that tripped the normalizer, and what normalization change (if any, beyond what S5-07
   already fixed) each example calls for.

**Done when (if not cut):** `docs/false_positive_analysis.md` explains the dominant false-positive
mechanism with concrete examples, per `project_plan.md` §9's specific callout that this deserves its
own section.

---

## Sprint 5 acceptance criteria

- `docs/failure_analysis.md` contains the Pareto chart, the two interventions, and a before/after
  table per category
- The trace viewer screenshot is good enough to sit at the top of the README
- At least one intervention shows a measurable, honestly-reported delta — including if it's small

---

## Sprint 5 exit checklist

- [ ] `/answer` (SSE streaming) and `/health` live, every request traced identically to the eval path
- [ ] Trace viewer renders a full per-query trace (all score types, latency, source-page links) —
      screenshot-ready
- [ ] Discrepancy report view live, or explicitly noted as cut in favor of the JSON endpoint
- [ ] Deployment status recorded in `docs/deployment.md` (public URL live, or explicitly cut with
      reasoning)
- [ ] `data/analysis/failure_labels.jsonl` — 100 labels, one primary code each, stratified
- [ ] `docs/assets/failure_pareto.png` + counts table, regenerates from the failure labels
- [ ] `docs/failure_analysis.md` — top-2 fixes described concretely, per-category before/after delta
- [ ] `docs/false_positive_analysis.md` present, or explicitly noted as cut
- [ ] CI still green: ruff + mypy + pytest (integration/db tests excluded) on every push

Do not start Sprint 6's release work until every box above is checked — S6-01's README leads with
this sprint's results table and trace-viewer screenshot, and S6-03's blog post leads with this
sprint's failure-analysis finding, not the architecture.
