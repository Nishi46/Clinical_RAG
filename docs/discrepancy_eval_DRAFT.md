# Discrepancy detection eval — DRAFT, NOT REAL GOLD

> **⚠ This is a mechanical sanity-check, not a real evaluation.** It's scored against
> `data/eval/t3_llm_draft_labels.jsonl` — this session's own LLM read of the worksheet text, explicitly
> **not** blind human adjudication (see `docs/t3_llm_draft_notes.md`). Using it as ground truth makes
> the numbers below an LLM checking an LLM, not an independent check on the detector. **Do not cite
> any number on this page as a real result.** It exists only to confirm `discrepancy_scorer.py` runs
> correctly end-to-end against real `data/discrepancy/reports/` output. The real report belongs at
> `docs/discrepancy_eval.md`, generated from `data/eval/t3_gold_labels.jsonl` once real hand-adjudication
> (S4-07) has actually happened.

Scored against 20 blind-adjudicated trials (S4-07). Positive class: `divergence`. `ambiguous` (human or detector) and detector `retrieval_failed` cases are excluded from precision/recall/F1 and reported separately below -- see `discrepancy_definition.md` SS3/SS4-05.

## Precision / recall / F1, per pair type and pooled

| Pair | n scored | Precision | 95% CI | Recall | 95% CI | F1 |
|---|---|---|---|---|---|---|
| First-posted vs. current registry | 17 | 0.800 | [0.376, 0.964] | 0.571 | [0.250, 0.842] | 0.667 |
| Current registry vs. protocol | 10 | 0.250 | [0.046, 0.699] | 1.000 | [0.207, 1.000] | 0.400 |
| Registry vs. results-reported | 20 | n/a | n/a | n/a | n/a | n/a |
| **Pooled** | 47 | 0.556 | [0.267, 0.811] | 0.625 | [0.306, 0.863] | 0.588 |

## Ambiguous bucket (excluded from the table above)

| Pair | gold/pred combination | n |
|---|---|---|
| First-posted vs. current registry | gold=ambiguous,pred=divergence | 2 |
| First-posted vs. current registry | gold=ambiguous,pred=match | 1 |
| Current registry vs. protocol | gold=ambiguous,pred=match | 5 |
| Pooled | gold=ambiguous,pred=divergence | 2 |
| Pooled | gold=ambiguous,pred=match | 6 |

## Retrieval failure / not applicable, per pair type

| Pair | retrieval_failed | not_applicable |
|---|---|---|
| First-posted vs. current registry | 0 | 0 |
| Current registry vs. protocol | 5 | 0 |
| Registry vs. results-reported | 0 | 0 |
