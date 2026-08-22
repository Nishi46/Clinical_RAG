# `protocol-drift` — Sprint Plan

Execution plan for the clinical-trial protocol RAG + discrepancy detection system. Companion to `protocol-drift-rag-scope.md`.

---

## Planning assumptions

| | |
|---|---|
| **Team** | 1 engineer, part-time |
| **Velocity** | ~16 hrs/week |
| **Sprint length** | 1 week |
| **Total** | Sprint 0 + 6 sprints ≈ 7 weeks, ~104 hrs |
| **Hardware** | Apple Silicon, 16GB+ · local Ollama, local embeddings, local reranker |
| **API budget** | **$0.** Free hosted tiers permitted for the T3 tier only, if a gate fails. |
| **Scarce resource** | Wall-clock inference hours and **your own labeling time** — the hand-adjudication in Sprint 4 is the real bottleneck, not compute |
| **Tracking** | GitHub Projects, one issue per task ID, milestones = sprints |

**Sprint ritual.** Monday: pull issues, re-estimate, cut what won't fit. Friday: 20-minute written retro in `docs/retros/`. Those notes become the blog post and your interview stories.

**Priority key:** 🔴 thesis-critical · 🟡 core · 🟢 cut first

---

## Epic map

| Epic | Description | Sprints |
|---|---|---|
| **E1 — Corpus & Harness** | Registry client, cohort freeze, PDF acquisition, trace store, Postgres | 1 |
| **E2 — Ingestion** | Page classification, OCR fallback, section segmentation, table extraction, chunking | 2 |
| **E3 — Retrieval** | Embeddings, hybrid search, RRF, prefilter, reranking, decomposition | 3 |
| **E4 — Evaluation** | T1–T4 question sets, scorers, judge calibration, ablation runs | 3–4 |
| **E5 — Discrepancy Detection** | Amendment history, outcome normalization, detector, adjudication | 4 |
| **E6 — Serving & Analysis** | API, trace viewer, failure taxonomy, Pareto, fixes | 5 |
| **E7 — Release** | README, blog, dataset, limitations | 6 |

---

## Sprint 0 — Reconnaissance (~8 hrs)

**Goal:** Eliminate the unknowns that would blow up Sprint 1. Do not skip — the document quality question determines whether the cohort selection works at all.

| ID | Task | Est | Pri |
|---|---|---|---|
| S0-01 | Explore API v2 by hand: `protocolSection` modules, `hasProtocol`/`hasSap` flags, results module, version history endpoint | 2h | 🔴 |
| S0-02 | Download ~20 protocol/SAP PDFs across different sponsors. Open them. **Look at them.** | 1.5h | 🔴 |
| S0-03 | Manually assess: what % have a text layer? How many are combined Protocol+SAP? How consistent are section headers across sponsors? | 2h | 🔴 |
| S0-04 | Pick therapeutic area based on S0-03 findings (oncology vs cardiovascular) | 0.5h | 🔴 |
| S0-05 | Read the outcome-switching literature — enough to define "discrepancy" precisely and defensibly | 1.5h | 🔴 |
| S0-06 | Set up Postgres + pgvector locally; pull Ollama models by digest | 0.5h | 🟡 |

**Exit criteria:** You can state the expected scanned-page rate, the section-header consistency problem in concrete terms, and a written definition of what counts as a discrepancy.

> **S0-03 is the highest-value two hours in the project.** Everything about ingestion difficulty flows from it, and it costs nothing to find out now instead of in Sprint 2.

---

## Sprint 1 — Corpus & Harness (E1)

**Sprint goal:** A frozen, reproducible corpus on disk with registry gold in Postgres and traces logging.

| ID | Task | Est | Pri | Deps |
|---|---|---|---|---|
| S1-01 | Repo scaffold: `pyproject.toml`, ruff, mypy, pytest, GitHub Actions | 2h | 🔴 | — |
| S1-02 | **API v2 client** — pagination, field selection, retry/backoff, polite rate limiting | 2.5h | 🔴 | S1-01 |
| S1-03 | **Cohort query + freeze** — filter (area, `hasProtocol OR hasSap`, results posted, ≥2017), stratify by sponsor type and phase, select 150–250 trials, write `data/cohort.json` manifest with NCT IDs | 2.5h | 🔴 | S1-02 |
| S1-04 | **Registry snapshot** — archive raw JSON per trial to disk, versioned and immutable | 1.5h | 🔴 | S1-03 |
| S1-05 | **Registry fact extraction** → Postgres `trials`, `outcomes`, `arms`, `eligibility`, `amendments` tables | 3h | 🔴 | S1-04 |
| S1-06 | **PDF downloader** — cached, resumable, polite; store by NCT ID + doc type | 2h | 🔴 | S1-03 |
| S1-07 | **Page classifier** — born-digital vs scanned via text-layer heuristic (PyMuPDF) | 2h | 🔴 | S1-06 |
| S1-08 | **Trace store** — SQLite/Postgres schema: `query`, `retrieval_step`, `chunk_hit`, `generation`, `cost_record` | 2.5h | 🔴 | S1-01 |
| S1-09 | **Corpus stats report** — page counts, scanned %, doc-type distribution, size histogram → `docs/corpus.md` | 1.5h | 🔴 | S1-07 |
| S1-10 | Tests: cohort determinism, trace integrity | 1h | 🟡 | S1-08 |

**Acceptance criteria**

- `data/cohort.json` is frozen; re-running selection produces byte-identical output
- Raw registry JSON archived — **the live registry mutates, and unarchived gold is unreproducible gold**
- `docs/corpus.md` reports the scanned-page rate
- Every model call (none yet) will route through a traced client

### 🚧 GATE S1-G1 — Scanned-page rate

Check `docs/corpus.md` before starting Sprint 2.

| Rate | Action |
|---|---|
| **< 15%** | Proceed. OCR is a footnote. |
| **15–40%** | Proceed, but budget S2-03 fully and report OCR'd content rate in every results table. |
| **> 40%** | **Re-select the cohort**, biasing toward sponsors with born-digital submissions. Do not let OCR become the project — it's a different project and a worse one. |

---

## Sprint 2 — Ingestion & Parsing (E2)

**Sprint goal:** Messy PDFs become clean, well-labeled chunks. This sprint is your technical differentiator.

| ID | Task | Est | Pri | Deps |
|---|---|---|---|---|
| S2-01 | Text extraction pipeline (PyMuPDF), layout-aware, preserving reading order | 2.5h | 🔴 | S1-07 |
| S2-02 | **Naive baseline chunker** — fixed 512-token, no structure. Your "before" picture. | 1h | 🔴 | S2-01 |
| S2-03 | OCR fallback for classified-scanned pages; flag chunks as OCR-derived | 2.5h | 🟡 | S1-07 |
| S2-04 | **Section segmentation** — detect protocol sections across inconsistent sponsor formats (heuristics + regex library + fallback) | 4h | 🔴 | S2-01 |
| S2-05 | **Table extraction with header propagation** — row/col headers preserved, caption line attached, units carried | 4h | 🔴 | S2-01 |
| S2-06 | **Assessment-schedule handling** — multi-page tables reassembled into one logical unit | 3h | 🔴 | S2-05 |
| S2-07 | **Amendment/version tagging** — extract document version + date; mark superseded content where detectable | 2.5h | 🔴 | S2-04 |
| S2-08 | **Section-aware chunker** — never split tables, prepend contextual header (NCT ID, doc type, version, section path) | 2.5h | 🔴 | S2-04, S2-05 |
| S2-09 | Metadata schema: `nct_id, doc_type, doc_version, section, subsection, page_range, chunk_type, is_ocr` | 1h | 🔴 | S2-08 |
| S2-10 | **Ingestion quality report** — section detection rate, tables found, chunks per doc, side-by-side naive vs section-aware on one assessment table → `docs/ingestion.md` | 2h | 🔴 | S2-08 |

**Acceptance criteria**

- Section detection succeeds on ≥80% of documents; failures logged with sponsor attribution
- No chunk splits a table mid-structure — enforced by a test
- `docs/ingestion.md` contains the naive-vs-section-aware screenshot pair (this goes in the blog post)
- Every chunk carries full metadata; `is_ocr` populated

**Time sink warning:** S2-04 and S2-05 will both want to eat the sprint. Timebox each. 80% section detection with 20% logged failures beats 95% and a blown sprint — and the failure list is itself analysis material.

---

## Sprint 3 — Retrieval Ladder + T1/T2 Eval (E3, E4)

**Sprint goal:** The ablation table exists. Every rung measured.

| ID | Task | Est | Pri | Deps |
|---|---|---|---|---|
| S3-01 | Embedding pipeline (local `sentence-transformers`), batched, cached by model digest | 2h | 🔴 | S2-08 |
| S3-02 | pgvector index + `tsvector` BM25 index + metadata columns | 2h | 🔴 | S3-01 |
| S3-03 | **T1 question generation** — auto from registry facts; record gold chunk IDs by locating each fact in the documents | 4h | 🔴 | S1-05, S2-08 |
| S3-04 | **T2 question set** — ~100 hand-written protocol-only questions with gold chunks | 4h | 🔴 | S2-08 |
| S3-05 | **Retrieval scorer** — Recall@{1,5,10,20}, Precision@k, MRR, nDCG@10 | 2h | 🔴 | S3-03 |
| S3-06 | **Answer generation** — grounded prompt, numbered chunks, span citations, explicit refusal path | 2.5h | 🔴 | S3-02 |
| S3-07 | **Correctness + faithfulness scorers** — exact/normalized for T1, atomic-claim grounding for faithfulness | 3h | 🔴 | S3-06 |
| S3-08 | **Judge calibration** — hand-label 50 T2 responses, compute Cohen's κ vs. judge, revise rubric if κ < 0.6 | 3h | 🔴 | S3-07 |
| S3-09 | Ladder rung 3: BM25 + RRF fusion (k=60) | 2h | 🔴 | S3-02 |
| S3-10 | Ladder rung 4: metadata prefilter (NCT ID / doc type / version parsed from query) | 2h | 🔴 | S3-09 |
| S3-11 | Ladder rung 5: cross-encoder rerank, `bge-reranker-v2-m3` local, 50 → 8 | 2.5h | 🔴 | S3-10 |
| S3-12 | **Ablation runs** — full eval at each of rungs 1–5; auto-generate `results/ablation.md` | 3h + wall | 🔴 | S3-11 |

**Acceptance criteria**

- Every T1 question has a gold chunk ID; retrieval recall is computable
- Results table regenerates from traces with one command — **no hand-copied numbers, ever**
- Latency broken out per stage (embed / dense / BM25 / fuse / rerank / generate)
- κ reported in `docs/judge_calibration.md`

### 🚧 GATE S3-G1 — Local model adequacy

After S3-07, check T1 correctness with the local 8B on *perfect* retrieval (feed gold chunks directly).

- **≥ 80%** → local model is fine for T1/T2. Proceed.
- **< 80%** → the model, not retrieval, is your ceiling. Swap to a more capable local model, or route generation to a free hosted tier and document the split.

Run this before the full ablation sweep. Discovering it afterward wastes several nights.

---

## Sprint 4 — Cross-Source & Discrepancy Detection (E4, E5)

**Sprint goal:** The headline capability. Also the sprint with the most human labeling — protect the time.

| ID | Task | Est | Pri | Deps |
|---|---|---|---|---|
| S4-01 | **Amendment history extraction** — registry version diffs: what changed, when, first-posted vs current primary outcome | 3h | 🔴 | S1-04 |
| S4-02 | **T4 question set** (~40) — amendment-aware, auto-generated from version history | 2h | 🔴 | S4-01 |
| S4-03 | **Outcome normalization layer** ⭐ — canonicalize measure names, timeframes, units so "OS at 2 years" ≡ "overall survival at 24 months" | 5h | 🔴 | S1-05 |
| S4-04 | **Query decomposition** — split cross-source questions into per-source sub-retrievals (ladder rung 6) | 3h | 🔴 | S3-11 |
| S4-05 | **Discrepancy detector** — retrieve protocol endpoint, compare to registry (first-posted + current) and results; emit structured verdict with citations to all sources | 4h | 🔴 | S4-03, S4-04 |
| S4-06 | **T3 question set** (~60) — cross-source comparison questions | 2.5h | 🔴 | S4-05 |
| S4-07 | **Hand-adjudication** — label 60 trials for true discrepancy status. Written rubric first. Blind to model output. | 5h | 🔴 | S4-06 |
| S4-08 | **Discrepancy scorer** — precision, recall, F1 vs. adjudicated labels; precision reported prominently | 2h | 🔴 | S4-07 |
| S4-09 | Adversarial set (~30 unanswerable) + refusal / over-refusal metrics | 2h | 🟡 | S3-06 |

**Acceptance criteria**

- Adjudication rubric written **before** labeling, committed, and followed
- Labeling done blind to system output — otherwise your ground truth is contaminated
- Discrepancy P/R/F1 reported with confidence intervals (n=60 is small; say so)
- Every flagged discrepancy carries citations to all three sources

**Two things to get right here:**

**S4-03 is the hard one and the interesting one.** Semantic equivalence between outcome phrasings will be your dominant false-positive source. Build it as a testable component with its own small labeled set (~100 phrase pairs) so you can report normalization accuracy separately. That subcomponent is a blog post on its own.

**S4-07 will overrun if you let it.** Five hours of careful labeling is realistic; ten is not available. If you fall behind, cut to 40 trials and report the smaller n honestly rather than rushing 60.

---

## Sprint 5 — Serving & Failure Analysis (E6)

**Sprint goal:** Failure analysis is the deliverable. Everything before this was setup.

| ID | Task | Est | Pri | Deps |
|---|---|---|---|---|
| S5-01 | FastAPI + SSE streaming answer endpoint | 2.5h | 🔴 | S3-06 |
| S5-02 | **Trace viewer** — per query: retrieved chunks with dense/BM25/rerank scores side by side, per-stage latency, source page links | 3.5h | 🔴 | S1-08 |
| S5-03 | **Discrepancy report view** — three-source comparison with citations | 2.5h | 🟡 | S4-05 |
| S5-04 | Deploy to a public URL (free tier) | 1.5h | 🟡 | S5-01 |
| S5-05 | **Label 100 failures** using the taxonomy (`R-MISS`, `T-MANGLE`, `V-AMEND`, `X-FALSEPOS`, …), stratified across tiers | 4h | 🔴 | S3-12, S4-08 |
| S5-06 | **Pareto chart** of failure categories | 1h | 🔴 | S5-05 |
| S5-07 | **Fix top 2 categories**, re-run full eval, report per-category delta | 4h + wall | 🔴 | S5-06 |
| S5-08 | Dedicated `X-FALSEPOS` analysis — why false discrepancies happen, what normalization fixed | 2h | 🟡 | S5-05 |

**Acceptance criteria**

- `docs/failure_analysis.md` contains the Pareto chart, the two interventions, and a before/after table **per category**
- The trace viewer screenshot is good enough to sit at the top of the README
- At least one intervention shows a measurable, honestly-reported delta — including if it's small

> This sprint produces the single most valuable sentence in the project: *"X was N% of failures; intervention Y cut it to M% and lifted end-to-end correctness Z points."*

---

## Sprint 6 — Release (E7)

| ID | Task | Est | Pri | Deps |
|---|---|---|---|---|
| S6-01 | README: results table + trace viewer screenshot above the fold, method second, reproduction third | 3h | 🔴 | S5-07 |
| S6-02 | `make reproduce` — clean clone → headline tables | 2h | 🔴 | S1-03 |
| S6-03 | **Blog post** — lead with the discrepancy finding and the failure analysis, not the architecture | 4h | 🔴 | S6-01 |
| S6-04 | 60-second demo GIF: a question answered with citations, then a flagged discrepancy | 1.5h | 🟡 | S5-03 |
| S6-05 | **Release the eval set** (HuggingFace) with a datasheet — T1–T4 + adversarial, ~430 questions with gold chunks | 3h | 🟡 | S4-06 |
| S6-06 | **Ethics + limitations section** — divergence ≠ misconduct, small n, single therapeutic area, OCR gaps, judge κ | 2h | 🔴 | — |
| S6-07 | Resume bullets with final real numbers | 0.5h | 🟡 | S6-01 |

**Acceptance criteria**

- A stranger reproduces the headline table from a clean clone
- Ethics section explicitly states discrepancies are candidates for human review, not accusations, and that legitimate reasons for outcome changes exist
- Limitations names ≥4 honest weaknesses
- No sponsor is named in a way that implies wrongdoing

---

## Definition of Done (every task)

1. Merged with type hints, ruff + mypy clean
2. New component has ≥1 test
3. All results in the trace store — **never** a number typed into markdown
4. Tables and figures regenerate via `scripts/`
5. Anything surprising written to `docs/retros/` the same day

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Registry data mutates mid-project | **Certain** | High | S1-04 archives raw JSON. Gold comes from the archive, never live. |
| Scanned-page rate too high | Med | High | Gate S1-G1; re-select cohort |
| Section segmentation harder than expected | High | Med | Timebox S2-04; 80% target with logged failures |
| Local model is the accuracy ceiling | Med | High | Gate S3-G1 before the ablation sweep |
| Hand-adjudication overruns | High | Med | Rubric first; cut to 40 trials rather than rush |
| Normalization false positives dominate | High | Med | S4-03 as a separately-tested component with its own labeled set |
| Adjudication contaminated by seeing model output | Med | **Critical** | Label blind. Enforce by labeling before running the detector. |
| Table extraction rabbit hole | High | Med | Timebox S2-05/06; log what fails |

---

## Cut list (in order)

1. S6-05 dataset release
2. S5-08 dedicated false-positive analysis
3. S4-09 adversarial set
4. S2-03 OCR fallback (if scan rate is low, just exclude those pages and report it)
5. S5-03 discrepancy report UI (JSON output is enough)
6. S4-07 adjudication 60 → 40 trials

**Never cut:** S1-04 (registry archive), S3-03 (gold chunk IDs), S3-08 (judge calibration), S5-05–S5-07 (failure analysis). Those four carry the entire thesis.

---

## Milestone checkpoints

| End of | You can say |
|---|---|
| Sprint 1 | "I froze a reproducible cohort and archived the registry gold before it could drift." |
| Sprint 2 | "I can show you what naive chunking does to a multi-page assessment schedule, and what mine does instead." |
| Sprint 3 | "Hybrid + reranking took Recall@10 from X to Y, and my judge agrees with me at κ = Z." |
| Sprint 4 | "I flag registry-vs-protocol endpoint divergence at N% precision against blind hand-adjudicated labels." |
| Sprint 5 | "My top failure category was X; I fixed it and here's the measured delta." |
| Sprint 6 | "Here's the repo, the eval set, and the writeup." |

The Sprint 4 line is the one that makes this project memorable. Sprint 5 is what makes it credible.

---

## Appendix — $0 execution notes

**Local stack:** embeddings via `sentence-transformers`, reranking via `bge-reranker-v2-m3` (~560M, comfortable on 16GB), generation and judging via an 8B Q4 through Ollama. Postgres + pgvector local. Nothing here needs a paid key.

**Throughput.** Embedding ~30k pages is a one-time overnight job. Reranking is cheap. Generation dominates: at ~6 calls per eval question and ~430 questions, one full eval pass is ~2,500 calls ≈ 3–5 hours on an 8B. **The ablation runs 5 passes** (one per rung), so S3-12 is roughly two overnight runs. Plan it for a Friday.

**Cache everything**, keyed on model digest + prompt hash. Re-running an unchanged rung must cost zero compute — you'll re-run constantly during failure analysis.

**Pin models by digest, not tag.** Ollama tags get republished. Record digests in `configs/models.yaml`. This is also your reproducibility argument in the README: open-weight models pinned by digest stay reproducible after hosted models are deprecated.

**Free-tier escape hatch.** If gate S3-G1 fails, route *generation only* for T2/T3 to Google AI Studio (~1,500 req/day, no card). At ~6 calls per question that's ~250 questions/day — one eval pass per day. Keep retrieval, reranking, and T1/T4 local. Document the split in the README; a hybrid stack chosen for measured reasons reads as engineering judgment, not compromise.