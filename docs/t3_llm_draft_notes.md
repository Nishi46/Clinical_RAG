# T3 LLM-drafted labels — status and caveats

**`data/eval/t3_llm_draft_labels.jsonl` is not gold data.** It is this session's own read of
`data/eval/t3_adjudication_worksheet.jsonl`'s 60 rows, applying `docs/adjudication_rubric.md`,
offered as a starting draft for a human reviewer to correct rather than write 60 judgments from
scratch. Every row carries `"label_source": "llm_draft_not_human_verified"` and the file is
deliberately named and located separately from `data/eval/t3_gold_labels.jsonl` — the real gold
path `scripts/export_gold_labels.py` writes to.

**Why this isn't a substitute for S4-07's real adjudication:** the entire point of hand-adjudication
is an independent check on `compare_outcomes`'s own LLM judge (S4-03) and `detect_discrepancies`
(S4-05) — both of which are themselves LLM judgments. Having an LLM (even a different, more capable
one than the local 8B judge those components use) produce the "gold" labels would make the eval
circular in substance even though it isn't circular in code: it would be one LLM's opinion checking
another LLM's opinion, dressed up as ground truth. `docs/discrepancy_eval.md` should never cite
numbers computed against this file as if it were `t3_gold_labels.jsonl`.

## How to use this file

1. Open `data/eval/t3_llm_draft_labels.jsonl` alongside `data/eval/t3_adjudication_worksheet.jsonl`
   (matched by `nct_id` + `pair`) and the source protocol/registry text.
2. For each row, decide: agree, correct the verdict, or correct the justification. Prioritize the
   **13 `ambiguous`** and **8 `divergence`** rows below — a `match` call is lower-risk to rubber-stamp
   than a call that something differs or can't be confidently classified.
3. Copy the corrected verdict + justification into `t3_adjudication_worksheet.jsonl`'s `label` field
   (this draft is *not* wired to write there automatically — that would blur the "human decided this"
   line the whole exercise exists to keep clean), along with real `time_spent_seconds`.
4. Run `scripts/export_gold_labels.py` once the worksheet is (partially or fully) filled in by a
   human, per the normal S4-07 path.

## Draft verdict distribution (n=60)

| Verdict | n |
|---|---|
| match | 39 |
| ambiguous | 13 |
| divergence | 8 |

By pair type:

| Pair | match | divergence | ambiguous |
|---|---|---|---|
| first_posted_vs_current | 10 | 7 | 3 |
| current_vs_protocol | 9 | 1 | 10 |
| registry_vs_results | 20 | 0 | 0 |

## Known limitations worth a human's attention

- **`registry_vs_results` is 20/20 `match`, and not a soft call** — verified by direct string
  comparison (not just my reading) that `registered_current` and `results_reported` measure/timeframe
  text is byte-identical for every sampled trial. This reflects a real property of this cohort
  (ClinicalTrials.gov auto-populates the results-section title from the current registration), not a
  drafting shortcut — but it also means this pair type contributes nothing to distinguishing
  match-detection skill in whatever P/R/F1 gets computed from the real gold file. Worth deciding
  explicitly whether `registry_vs_results` should be resampled from a different cohort slice in a
  future round, or reported as "not discriminating in this cohort" rather than silently averaged in.
- **10 of `current_vs_protocol`'s 20 rows are `ambiguous`**, and half of those are retrieval failures
  (`protocol_excerpts` empty) rather than genuine content ambiguity — `docs/discrepancy_eval.md`-style
  reporting should keep that split visible (S4-05's own `retrieval_failed` flag distinguishes them at
  the detector level; this draft's `ambiguous` label collapses both into one verdict, since that's the
  only label the rubric gives a human for "couldn't compare" — worth deciding whether the real
  adjudication should record retrieval failure as its own value in `t3_gold_labels.jsonl` rather than
  folding it into `ambiguous`, since S4-08's scorer already treats `retrieval_failed` as a third state
  distinct from `ambiguous` on the *predicted* side).
- **A few close calls the rubric doesn't cleanly resolve**, worth a second look specifically:
  `NCT03085914` (Phase 1 → Phases 1 & 2 population scope for a safety outcome — could be routine
  trial-design completion, not drift), `NCT03020017` (28→21 day timeframe change — small enough that
  it might be a data update rather than a redefinition), and `NCT03025880` (first-posted's own
  timeframe field looks internally inconsistent with its own measure name, independent of anything
  current changed).
- **Two rows relied on outside domain knowledge not present in the worksheet text**: `NCT03037385`
  and its `current_vs_protocol` counterpart were called `match` specifically because "BLU-667" is the
  known development code name later assigned the generic name "pralsetinib" — the protocol excerpt
  confirms this (it uses "pralsetinib" too), but a labeler without that background could reasonably
  read it as a drug substitution and call it `divergence`. Flagging this explicitly since it's exactly
  the kind of external-knowledge dependency a rubric can't fully specify in advance.
