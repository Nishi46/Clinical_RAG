# Corpus stats — Sprint 1 frozen cohort

Regenerated from `data/cohort.json`, `data/pdfs/manifest.json`, and `data/corpus_classification.json` — no number below is hand-typed. Regenerate with `make corpus-report`.

## Overview

| Metric | Value |
|---|---|
| Trials in frozen cohort | 200 |
| Documents downloaded | 313 |
| Documents classified | 312 |
| Documents failed to classify (malformed PDF) | 1 |
| Total pages | 18377 |

## Scanned-page rate

| Level | Rate |
|---|---|
| Page-level (scanned pages / total pages) | 2.69% |
| Document-level, fully born-digital | 79.5% |

Document-level classification counts: born_digital=248, mixed=59, scanned=5

## vs. Sprint 0 sample

S0-03 measured this on a smaller sample (20 trials / 34 documents, no therapeutic-area filter) before the cohort was frozen. Comparing against the real, full 200-trial cohort:

| Metric | S0-03 sample | Full cohort |
|---|---|---|
| Page-level scanned rate | 1.1% | 2.69% |
| Document-level born-digital rate | 88.0% | 79.5% |

**Note:** page-level scanned rate: full-cohort 2.69% diverges >2x from S0-03 sample 1.1%

## 🚧 GATE S1-G1 — Scanned-page rate

Measured page-level scanned rate: **2.69%** → bracket **< 15%**.

**Action: Proceed. OCR is a footnote.**

## Page-count distribution

| min | median | mean | max |
|---|---|---|---|
| 1 | 41.5 | 58.9 | 412 |

### Histogram (pages per document, 50-page buckets)

| Range | Documents |
|---|---|
| 0-49 | 176 |
| 50-99 | 80 |
| 100-149 | 37 |
| 150-199 | 13 |
| 200-249 | 3 |
| 300-349 | 2 |
| 400-449 | 1 |

## Document-type distribution

| Doc type | Count |
|---|---|
| icf | 36 |
| protocol | 203 |
| sap | 74 |

## Cohort stratification (sponsor class × phase)

| Stratum | Trials |
|---|---|
| FED\|NA | 1 |
| INDUSTRY\|NA | 13 |
| INDUSTRY\|PHASE1 | 11 |
| INDUSTRY\|PHASE1\|PHASE2 | 9 |
| INDUSTRY\|PHASE2 | 23 |
| INDUSTRY\|PHASE2\|PHASE3 | 1 |
| INDUSTRY\|PHASE3 | 20 |
| INDUSTRY\|PHASE4 | 3 |
| NETWORK\|PHASE2 | 1 |
| NIH\|PHASE1 | 2 |
| NIH\|PHASE1\|PHASE2 | 1 |
| NIH\|PHASE2 | 3 |
| OTHER\|EARLY_PHASE1 | 2 |
| OTHER\|NA | 53 |
| OTHER\|PHASE1 | 7 |
| OTHER\|PHASE1\|PHASE2 | 8 |
| OTHER\|PHASE2 | 35 |
| OTHER\|PHASE2\|PHASE3 | 1 |
| OTHER\|PHASE3 | 3 |
| OTHER\|PHASE4 | 3 |

