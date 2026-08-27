# Ingestion quality report — Sprint 2

Regenerated from `data/sections/`, `data/tables/`, `data/chunks/`, `data/chunks_naive/`, and `data/ocr_backlog.json` — no number below is hand-typed. Regenerate with `make ingestion-report`.

## Section detection

| Metric | Value |
|---|---|
| Documents | 276 |
| ≥1 canonical section detected | 222 (80.4%) |
| Fully unclassified (0 canonical sections) | 54 |

Every fully-unclassified document is logged with its sponsor name and sponsor class in `data/sections/detection_failures.log`.

Sprint 2 acceptance criteria requires ≥80% section-detection rate — measured **80.4%**.

### By sponsor class

| Sponsor class | Documents | Detected | Rate |
|---|---|---|---|
| FED | 1 | 1 | 100.0% |
| INDUSTRY | 144 | 116 | 80.6% |
| NETWORK | 1 | 1 | 100.0% |
| NIH | 6 | 4 | 66.7% |
| OTHER | 124 | 100 | 80.6% |

## Document-depth split

The corpus mixes full protocols/SAPs against thin 2-3 page academic summaries with no assessment table at all (`corpus_assessment.md` Sec.4) — visible here as a number, not just a note.

| Bucket | Documents | Share |
|---|---|---|
| Zero sections detected | 54 | 19.6% |
| Full section coverage (no unclassified gap) | 4 | 1.4% |
| Partial | 218 | 79.0% |

## Tables

| Metric | Value |
|---|---|
| Documents | 276 |
| Documents with ≥1 table | 239 |
| Raw per-page tables | 4721 |
| Reassembled logical tables | 4002 |
| Multi-page runs collapsed by S2-06 | 719 |

## Chunks

| Metric | Value |
|---|---|
| Documents chunked | 276 |
| Total chunks | 30192 |
| Chunks per document (mean) | 109.4 |
| Chunks per document (median) | 73.0 |
| `is_ocr` chunks | 38 |

### Chunk-type breakdown

| Type | Count |
|---|---|
| assessment_schedule | 126 |
| table | 4002 |
| text | 26064 |

## OCR backlog

`data/ocr_backlog.json` enumerates **465** page(s) across **61** document(s) needing OCR; the default pipeline skips and reports them rather than attempting extraction (S2-03).

## Naive vs. section-aware: NCT02872116's assessment-schedule table

`Table 5.1-3` (`corpus_assessment.md` Sec.6) spans 0-indexed pages 86-90. S2-02's naive 512-token chunker has no table awareness and cuts straight through it; S2-08's section-aware chunker keeps the whole table as one chunk.

### Naive chunker (`data/chunks_naive/`) — table torn across 3 chunk(s)

| Chunk | Pages | Tokens |
|---|---|---|
| 63 | 85-88 | 512 |
| 64 | 88-90 | 512 |
| 65 | 90-93 | 512 |

The seam between chunks 63 and 64 lands mid-sentence inside a single table cell -- the mangled cut that's the whole point of this comparison:

```
...beyond(Every 2 weeks) beyond(Every 3 weeks) on Day 1 plus-FOLFOX Cycle 1 Day 1 on Day 1a (C1D1) Efficacy Assessments Tumor Imaging AssessmentSee NoteSee NoteCT/MRI scan of chest, abdomen, pelvis, and any clinically indicated sites. Every 6 weeks (7 days) from first dose up to and including Week 48,
---- chunk boundary ----
then every 12 weeks (7 days) regardless of treatment schedule until disease progression (unless treatment beyond PD is permitted; see Section 4.5.1.6), or the subject withdraws consent, whichever comes first. Subjects who discontinue study treatment for reasons other than PD will continue to have t...
```

### Section-aware chunker (`data/chunks/`) — 1 clean chunk(s)

**Chunk 83**, `chunk_type=assessment_schedule`, pages 86-90:

```
[NCT02872116 | protocol v9 | assessment_schedule]
Table 5.1-3: On-Treatment Assessments - Subjects in Nivolumab-plus-Chemotherapy (XELOX or FOLFOX) Arm
(CA209649)
Procedure | Nivolumab-
plus-XELOX
and
Nivolumab-
plus-
FOLFOX
Cycle 1 Day 1
(C1D1) (C1D1) | Nivolumab-plus-
XELOX
Cycle 2 and
beyond
(Every 3 weeks)
on Day 1 | Nivolumab-plus-
FOLFOX
Cycle 2 and
beyond
(Every 2 weeks)
a
on Day 1 | Note
*Treatment till PD, unacceptable toxicity, withdraw
from IC
Safety Assessments |  |  |  | 
Targeted Physical Examination | X | X | X | To be performed within 72 hours of dosing.
Vital Signs | X | X | X…
```

