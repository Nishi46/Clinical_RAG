# `protocol-drift` — Production RAG with Evals, Build Scope

**One line:** A retrieval system over clinical trial protocol documents that answers questions *and* flags where a trial's registered promises diverge from what its protocol and results actually say.

**Target:** 5–6 weeks part-time (~100 hrs). A 40-hour subset is marked ⚡. Runs at **$0**.

---

## 1. Why not SEC filings

You called it. The saturation is worse than "some people have done it":

- **`run-llama/sec-insights`** is LlamaIndex's official full-stack reference app, deployed at secinsights.ai. Building an SEC RAG puts you in direct comparison with a framework vendor's showcase.
- A dozen near-identical public repos: LangGraph 10-K agents, EDGAR Streamlit chatbots, "Finbot," university capstones. Same corpus, same stack, same demo.

The XBRL answer-key insight was the good part of that plan. **This project keeps that insight and moves it to a corpus nobody has claimed.**

---

## 2. The corpus: ClinicalTrials.gov

Every registered trial has two representations of the same reality:

| | |
|---|---|
| **Structured** | Registry record via [API v2](https://clinicaltrials.gov/data-api/about-api) — `protocolSection` with ~12 nested modules: phase, enrollment, sponsor, eligibility, primary/secondary outcome measures with timeframes, arms, interventions. Plus a full **amendment history** of what changed and when. |
| **Unstructured** | Sponsor-posted **protocol PDFs and Statistical Analysis Plans** at `cdn.clinicaltrials.gov/large-docs/...`. Typically 100–300 pages. Flagged in the API by `hasProtocol` / `hasSap` so you can filter to trials that have them. |

Same asymmetry that made SEC work: **the structured side is your authoritative answer key; the unstructured side is a genuinely hard retrieval problem.** Except nobody's built it.

### What makes these documents actually messy

Not "messy" as a marketing word — messy in ways that will break your pipeline and give you real failure modes to analyze:

- **Born-digital and scanned PDFs mixed**, sometimes within one document
- **Assessment schedule tables** spanning multiple pages, with visit columns and procedure rows — the single hardest structure in the corpus
- **Amendment layering** — v1.0 through v7.0, with superseded text sometimes retained
- **Combined Protocol+SAP documents** where section numbering restarts mid-file
- **Redactions** (black boxes, removed pages) creating silent content gaps
- **Inconsistent section naming** across sponsors for the same concept
- **Dense medical abbreviation** with sponsor-specific glossaries
- **Eligibility criteria as nested logic** rendered as flat bullets

---

## 3. The differentiator: this isn't a Q&A chatbot

**Outcome switching is a real, documented problem in medical research** — trials registering one primary outcome and reporting a different one. It's the subject of published cross-sectional studies and the reason the registry mandates amendment tracking in the first place.

So the system does something a chatbot doesn't:

> **Given a trial, retrieve what the protocol says the primary outcome is, compare it to what the registry recorded at first posting, compare both to what the results section reports — and flag the divergence with citations to all three.**

That reframes the project entirely:

| Ordinary RAG project | This |
|---|---|
| "Ask questions about documents" | "Detect where sources contradict each other" |
| Ground truth is LLM-generated | Ground truth is the federal registry |
| Success = plausible answer | Success = a verifiable, cited discrepancy |
| Demo | Research-integrity tool |

**The headline README line writes itself:** *"Scanned 200 trial protocols against their registry records and flagged N primary-outcome discrepancies, with precision X% against hand-adjudicated labels."*

That is not a project anyone will confuse with a tutorial.

---

## 4. Scope boundaries

**In**

- 150–250 trials in **one therapeutic area** (oncology or cardiovascular — dense shared vocabulary makes retrieval harder, which is the point) ⚡
- Filter to trials where `hasProtocol` or `hasSap` is true *and* results are posted
- Section-aware PDF ingestion with dedicated table handling
- Hybrid retrieval (dense + BM25), metadata filtering, cross-encoder reranking ⚡
- ~400-question eval set, 4 tiers, ground truth from the registry API ⚡
- Discrepancy detection as a distinct evaluated task
- Failure taxonomy from 100 hand-labeled errors
- Deployed demo with trace viewer + discrepancy report

**Out**

- Pre-2017 trials (document posting was sparser)
- OCR quality improvement research (detect scanned pages, log them, move on)
- Linking to published papers (registry + protocol + results postings only — paper linkage is a stretch goal)
- Any clinical interpretation of trial content

---

## 5. Ethics note — put this in the README

Medical domain, so state boundaries explicitly. This is judgment signal, not boilerplate.

- **This is a research-integrity tooling project, not a clinical tool.** No treatment recommendations, no interpretation of trial results as medical evidence.
- **Flagged discrepancies are candidates for human expert review, never accusations.** Registered outcomes change for legitimate reasons — protocol amendments, regulatory feedback, safety findings. Your system detects *divergence*, and a human decides whether it's a problem.
- **Don't name and shame.** Report aggregate rates and use anonymized or NCT-ID-only examples in the writeup. Do not build a public dashboard ranking sponsors.
- All data is public federal record; no PHI is involved. Say so.

A reviewer seeing this section learns you can be trusted with a sensitive domain. That's worth more than another percentage point of recall.

---

## 6. Architecture

```
ClinicalTrials.gov API v2 ──┬──▶ registry records (structured)  ──▶ EVAL GOLD + comparison target
                            │
                            └──▶ protocol / SAP PDFs (cdn)
                                          ↓
                            [Ingest] page classify (born-digital vs scanned)
                                          ↓  → OCR fallback
                            [Parse]  section detect · table extract · amendment tag
                                          ↓
                            [Chunk]  section-aware · tables intact · contextual header
                                          ↓
                            [Index]  dense + BM25 + metadata (Postgres/pgvector)
                                          ↓
                            [Retrieve] prefilter → hybrid → RRF → rerank
                                          ↓
                    ┌─────────────────────┴─────────────────────┐
              [Answer]  grounded QA              [Compare]  discrepancy detector
                    └─────────────────────┬─────────────────────┘
                                     [Trace store]
```

**Build the trace store in week 1.** Every metric and every failure label reads from it. Retrofitting observability is how these projects die.

---

## 7. Eval design

### 7.1 Question tiers (~400 + adversarial)

| Tier | Count | Source | Grading | Example |
|---|---|---|---|---|
| **T1 — Registry-verifiable** ⚡ | ~200 | Auto-generated from API fields | Exact / normalized match | "What is the target enrollment for NCT…?" · "What is the primary outcome timeframe?" |
| **T2 — Protocol-only** ⚡ | ~100 | Hand + templated, answers in PDF only | Judge + spot-check | "What are the dose-modification rules for grade 3 neutropenia?" |
| **T3 — Cross-source comparison** ⭐ | ~60 | Registry vs. protocol vs. results | Structured verdict + citations | "Does the protocol's stated primary endpoint match the registry record?" |
| **T4 — Amendment-aware** | ~40 | Registry version history | Exact match | "Was the primary outcome changed after first posting? When, and to what?" |
| **Adversarial** | ~30 | Unanswerable from corpus | Refusal expected | Questions about unposted documents, or facts no protocol contains |

T1 gives you **retrieval ground truth for free** — record which document section contains each registry fact, so you can compute recall, not just answer correctness. Without gold chunk IDs you cannot explain *why* anything failed.

T3 and T4 are the tiers nobody else has. They're also where the interesting failures live.

### 7.2 Metrics

**Retrieval** — Recall@{1,5,10,20}, Precision@k, MRR, nDCG@10.
Recall@k is the hard ceiling on everything downstream. Lead the README with it.

**Generation** — answer correctness (exact for T1/T4, judged for T2), **faithfulness** via atomic-claim grounding, refusal accuracy on adversarial, and over-refusal on answerable.

**Discrepancy detection** (the headline task) — precision, recall, and F1 against **hand-adjudicated labels on ~60 trials**. Report precision prominently: a false discrepancy accusation is far more costly than a miss, and saying so demonstrates you understand the domain's asymmetry.

**Operational** — p50/p95/p99 latency broken out per stage, cost per query, tokens. On a $0 stack, also report tokens/sec locally so numbers are interpretable.

### 7.3 Calibrate the judge

Hand-label 50 T2 responses yourself. Run the judge on the same 50. Report **Cohen's κ**. If κ < 0.6, revise the rubric and re-measure. Two sentences about judge calibration puts you ahead of nearly every candidate, because it shows you know the eval is itself a system that can be wrong.

---

## 8. The retrieval ladder

Build in order, **run the full eval after each rung**. Each becomes a row in the ablation table. This sequencing is the project.

1. Fixed-size chunks, dense only — the floor ⚡
2. Section-aware chunks + contextual headers ⚡
3. + BM25 hybrid via RRF (k=60) ⚡ — exact-match matters enormously for drug names, dose units, outcome measure names
4. + metadata prefilter (NCT ID, document type, amendment version)
5. + cross-encoder reranking: retrieve 50 → rerank → top 8 ⚡
6. + query decomposition for T3/T4 multi-source questions

> **Free first failure mode:** run naive 512-token chunking on an assessment-schedule table and screenshot what comes out. That's your "before" picture and it's genuinely striking.

---

## 9. Failure taxonomy

Sample 100 failures, stratified across tiers, one primary label each:

| Code | Failure | Typical fix |
|---|---|---|
| `R-MISS` | Gold chunk never retrieved | Chunking, embeddings, query rewrite |
| `R-DISTRACT` | Outranked by near-duplicate from another trial or arm | Metadata filter, reranking |
| `T-MANGLE` | Assessment-schedule table destroyed; value read from wrong row/column | Table serialization |
| `T-SCAN` | Content in a scanned page never extracted | OCR fallback, page classification |
| `V-AMEND` | Answer pulled from a superseded protocol version | Amendment tagging |
| `E-ARM` | Confused study arms or cohorts | Arm-aware metadata |
| `X-PARTIAL` | Found 2 of 3 sources needed for a comparison | Multi-hop retrieval |
| `X-FALSEPOS` | Flagged a discrepancy that isn't one ⭐ | Comparison prompt, normalization |
| `G-HALLUC` | Claim absent from retrieved context | Grounding constraints |
| `G-UNIT` | Right value, wrong unit or timeframe | Header propagation |
| `A-OVERREFUSE` | Refused an answerable question | Prompt calibration |

**Then do the part that matters:** Pareto chart, fix the top two, re-run, report the per-category delta. `X-FALSEPOS` deserves its own section — semantically equivalent outcome phrasings ("overall survival at 24 months" vs "OS at 2 years") are the dominant false-positive source, and solving that is a genuinely interesting normalization problem.

---

## 10. Results table (top of README)

| Config | Recall@10 | nDCG@10 | Correctness T1 | Faithfulness | Discrepancy P/R | p95 (ms) |
|---|---|---|---|---|---|---|
| Fixed chunks, dense only | | | | | | |
| + section-aware chunking | | | | | | |
| + BM25 hybrid (RRF) | | | | | | |
| + metadata prefilter | | | | | | |
| + cross-encoder rerank | | | | | | |
| + query decomposition | | | | | | |

---

## 11. Tech stack — $0

| Layer | Pick | Why |
|---|---|---|
| PDF parse | PyMuPDF + page classifier | Fast; detect scanned pages and route them |
| OCR fallback | Tesseract or a local OCR model | Only for classified-scanned pages |
| Tables | Custom extraction w/ header propagation | Your differentiator; don't outsource it |
| Store | **Postgres + pgvector** | Dense + BM25 (`tsvector`) + metadata filters in one system. Interviewers respect this over a managed vector DB, and hybrid fusion becomes trivial SQL. |
| Embeddings | Local open model via `sentence-transformers` | Free, pinned, reproducible |
| Reranker | `bge-reranker-v2-m3` locally | Strong open cross-encoder, ~560M params |
| Generation | Ollama 8B Q4 on Apple Silicon; free tiers for a larger model on headline configs | $0 |
| Judge | Local 8B, calibrated against your labels | Free and keeps judge capability constant |
| Orchestration | **Plain Python** | ~200 lines. "I used LangChain" invites the follow-up you don't want. |
| Serving | FastAPI + SSE | |

**$0 notes:** cache every model call keyed on model digest; expect overnight runs for full sweeps; a local 8B is adequate for T1/T4 (extraction-heavy) but check T2/T3 quality early — if the local model can't do cross-source comparison reliably, route only T3 to a free-tier larger model and document the split.

---

## 12. Phases

| Week | Focus | Milestone |
|---|---|---|
| 1 ⚡ | Registry pull, PDF download, trace store, page classifier | Corpus on disk; registry facts in Postgres; traces logging |
| 1–2 ⚡ | Section segmentation, table extraction, chunking, metadata | Naive-vs-section-aware comparison documented |
| 2–3 ⚡ | Retrieval ladder rungs 1–5, eval after each | First ablation table |
| 3–4 | T3/T4 eval tiers + discrepancy detector | Discrepancy P/R on 60 hand-adjudicated trials |
| 4 | Serving, trace viewer, discrepancy report UI | Deployed URL |
| 5 | Failure analysis: 100 labels, Pareto, fix top 2, re-measure | Before/after deltas per category |
| 6 | README, blog post, demo GIF, dataset release | Shipped |

---

## 13. Resume bullets

- Built a retrieval system over 200 clinical trial protocols (~30k pages of mixed born-digital and scanned PDF); hybrid dense+BM25 retrieval with cross-encoder reranking lifted Recall@10 from 58% → 87%
- Designed a 400-question eval suite with ground truth derived from the ClinicalTrials.gov registry API; calibrated the LLM judge against human labels at κ = 0.71
- Built a cross-source discrepancy detector that flags divergence between registered and protocol-stated primary outcomes, achieving 84% precision against hand-adjudicated labels across 60 trials
- Reduced table-extraction failures 41% → 9% by preserving multi-page assessment-schedule structure, the largest single failure category in the error taxonomy

*(Illustrative — the shape is what matters: baseline → intervention → measured delta.)*

---

## 14. Traps

- **Building the architecture before the first eval.** Then there's no ablation table, which is most of the value. Eval from day one on 20 questions.
- **Corpus too large.** 200 trials is plenty. 2,000 makes every iteration slow and buys nothing.
- **Skipping gold chunk IDs.** No retrieval recall, no explanations.
- **Treating scanned pages as a research project.** Classify, OCR, log the rate, move on.
- **Letting discrepancy detection become the whole project.** It's the headline, but the retrieval quality underneath is what makes it credible. Rungs 1–5 first.
- **Framing it as catching fraud.** It detects divergence. Humans adjudicate. Get this wrong and a domain-expert interviewer will stop taking you seriously.

---

## 15. Interview questions this prepares you for

- How did you build ground truth, and how do you know your judge is calibrated?
- What's your retrieval recall ceiling, and how does it bound end-to-end accuracy?
- Walk me through your dominant failure mode and what you did about it.
- Why hybrid search over dense alone — where specifically did BM25 win?
- How do you handle semantically equivalent phrasings when comparing two sources?
- Why is precision more important than recall for your discrepancy task?
- What would break first at 10,000 trials?

---

## Data sources

- ClinicalTrials.gov API v2 — [clinicaltrials.gov/data-api/about-api](https://clinicaltrials.gov/data-api/about-api)
- Posted protocol / SAP documents — `cdn.clinicaltrials.gov/large-docs/...`, filterable via `hasProtocol` / `hasSap`
- Background on outcome switching — [Prevalence of primary outcome changes in registered trials](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4032105/)

### Backup corpus, if you want a non-medical option

**NTSB CAROL** ([data.ntsb.gov/carol-main-public](https://data.ntsb.gov/carol-main-public/)) — aviation accident investigations, 1962–present, with 16 common + 24 aviation-specific coded fields alongside full narrative reports and probable-cause text. Bulk download (`avall.zip`) and a REST API. Same structured-key-plus-messy-text property, similarly unclaimed. Weaker on document difficulty — narratives are shorter and cleaner than trial protocols — but no medical-domain sensitivity to navigate.