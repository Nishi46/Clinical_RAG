# T3 adjudication rubric (S4-07)

Written before any hand-labeling, per the same rubric-first discipline `docs/judge_rubric.md`
established for S3-08 and per S4-07 step 1's own requirement — commit this file before labeling a
single trial. Directly operationalizes `discrepancy_definition.md` §3's match/divergence/ambiguous
table into a checklist a human can follow consistently trial-to-trial, for the three pairwise
comparisons in §2's table (first-posted-vs-current, current-vs-protocol, registry-vs-results).

**Labeling is blind.** Read only the source texts on the worksheet row for the specific pair being
labeled (`scripts/build_adjudication_worksheet.py`'s output — registry outcome text pulled fresh
from Postgres, protocol excerpts pulled fresh from the `chunks` table). Never open
`data/discrepancy/reports/`, never import or read `protocol_drift.discrepancy`, never look at
S4-05's code before finishing all 60 rows. The worksheet-building script itself has no import path
to the detector, enforcing this mechanically — but the discipline still matters: don't go looking
for the detector's answer out of curiosity mid-session.

## The three labels

| Label | Apply when |
|---|---|
| **Match** | Both texts describe the same clinical construct (e.g. both are "overall survival," possibly worded differently), the same population/comparator, and the same timeframe — or a timeframe difference attributable only to unit/format (e.g. "24 months" vs. "2 years" is a match, not a divergence). Different phrasing of the identical clinical fact is a match. |
| **Divergence** | The two texts describe a different clinical construct (e.g. overall survival → progression-free survival), a different comparator/treatment arm, a materially different population (different disease stage, different biomarker threshold, different age cutoff), or a different primary-vs-secondary designation for what's otherwise the same measure. |
| **Ambiguous** | Wording changed enough that you cannot confidently call it match or divergence either way; one side lists multiple primary outcomes and the other lists only one (partial overlap, and you can't tell if the single one is the "same" outcome or a different one); or the protocol excerpt genuinely isn't there to compare against (see the retrieval-failure procedure below — this is the one case with its own specific step, not just "when unsure"). |

## Step-by-step procedure, per row

1. **Read the question and the pair type first** (`current_vs_protocol` /
   `first_posted_vs_current` / `registry_vs_results`) so you know which two texts you're actually
   comparing — the worksheet only shows the sources relevant to that pair.
2. **Read every source text on the row in full** before forming a judgment. For
   `current_vs_protocol`, that means reading every protocol excerpt provided, not just the first —
   a trial can have several `objectives`/`synopsis` chunks and the real endpoint statement isn't
   always in the first one.
3. **Retrieval-failure check (current_vs_protocol only):** if `protocol_excerpts` is empty, look at
   `protocol_available_sections` before concluding anything.
   - If the list is also empty or clearly has nothing relevant (e.g. only `eligibility`,
     `administrative`), label **ambiguous** with a justification noting no protocol endpoint text
     was locatable — this is a retrieval failure, not evidence of divergence, per
     `discrepancy_definition.md` §3's explicit rule. Do not guess or default to divergence.
   - If a plausibly relevant section label is present (e.g. `study_design`, `synopsis` under a
     different heading than expected) that the worksheet didn't surface, note this in the
     justification as a real gap worth reporting in the retro (`docs/retros/`) — still label
     **ambiguous** for this row, since you're not being handed that text to actually compare
     against.
4. **Compare against the table above.** Apply construct, then population/comparator, then
   timeframe, in that order — a construct mismatch is decisive regardless of population/timeframe
   agreement; only fall through to population/timeframe comparison once construct matches.
5. **Write a one-sentence justification** stating *what* matched or differed in concrete terms
   (e.g. "comparator arm changed from ipilimumab-combination to chemotherapy-combination"), not a
   restatement of the label itself ("this is a divergence" is not a justification).
6. **Log time spent on this row** (`label.time_spent_seconds`) before moving to the next — a
   running total, not a reconstruction at the end.

## Boundary cases, decided in advance

- **Abbreviation/rewording of the identical construct** ("Overall Survival (OS)" vs. "OS"; "PFS"
  vs. "Progression-free survival"): match. This is exactly SS1's stated dominant false-positive
  source in the literature — don't let surface wording differences drive the label.
- **A population/comparator detail is added but doesn't change who's being studied or compared**
  (e.g. a parenthetical clarifying an already-implied inclusion criterion): match, not divergence —
  the bar is *materially* different, not *textually* different.
- **A genuine timeframe change** (e.g. "12 months" → "24 months", not a unit/format rewording of
  the same duration like "24 months" ≡ "2 years"): divergence, since the numeric duration actually
  changed. This is *not* explicitly named in `discrepancy_definition.md` §3's divergence row —
  treat a real duration change as divergence by extension of "materially different" scope, and note
  in the justification that this is an extension, not a table entry, so a reader of
  `docs/discrepancy_eval.md` can see where the rubric had to make a judgment call the source
  document didn't spell out.
- **Second primary outcome added, first one unchanged** (the real `NCT02872116`/CheckMate-649
  pattern also involves a comparator/threshold change, but a case that *only* added a second
  primary outcome with the first left untouched): ambiguous — partial overlap, per §3's explicit
  ambiguous criterion, not a clean match (something changed) and not a clean divergence (the
  original outcome itself didn't change).
- **`registered_first` or `results_reported` text is missing entirely for this row's pair**: this
  shouldn't occur — `t3_questions.py` only generates a question for a pair with data available —
  but if it does, label ambiguous with a justification noting the missing source, and flag it in
  the retro as a data-availability bug, not a real ambiguous case.

## Scope note

This rubric grades **whether two outcome-measure texts describe the same thing**, mirroring
`compare_outcomes`'s job (S4-03) and `detect_discrepancies`'s job (S4-05) — that's the point: this
is the independent check on whether those automated components get it right, so it must be applied
without looking at what they concluded. It is silent on *why* a divergence exists (amendment
rationale, safety finding, etc.) — per `discrepancy_definition.md` §4's ethics stance, that's out of
scope for this project entirely, not just for this rubric.
