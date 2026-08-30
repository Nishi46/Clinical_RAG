# Gate S3-G1 — Local model adequacy

Per `sprint_3_implementation.md`'s gate: T1's 200 questions fed their own real gold chunks directly
(retrieval skipped entirely, via `fetch_chunks` + `generate_answer`), scored with `exact_match_score`
(S3-07) — isolates generation quality from retrieval quality. Run *after* the S3-12 ablation sweep
had already completed (out of the doc's recommended order), so this is a retroactive check rather
than the sweep-avoiding check it's designed to be — the finding below has retroactive implications
for S3-12's own T1 correctness column, noted at the end.

## Headline result

**80/200 = 40.0% correctness.** Below the 80% threshold, which per the gate's own decision table
triggers the cloud-escape-hatch consideration. **But the literal number is misleading** — see below
before treating this as "the local model is inadequate."

| | |
|---|---|
| Correct | 80 / 200 (40.0%) |
| Refusals (`NOT_ANSWERABLE` despite being given the gold chunk) | 51 / 200 (25.5%) |

By template:

| Template | Correct | Refusals |
|---|---|---|
| min_age | 24/25 (96.0%) | 0 |
| max_age | 19/23 (82.6%) | 2 |
| sponsor | 11/25 (44.0%) | 6 |
| phase | 10/26 (38.5%) | 1 |
| primary_outcome_timeframe | 7/25 (28.0%) | 9 |
| arm_label | 4/25 (16.0%) | 11 |
| primary_outcome_measure | 3/25 (12.0%) | 6 |
| enrollment_count | 2/26 (7.7%) | 16 |

## Root cause: this is mostly a gold-chunk citation problem, not a generation problem

The per-template spread (96% down to 8%) is the first clue this isn't uniform model incapability.
Manually inspecting the wrong/refused answers' actual gold chunks reveals two distinct, largely
unrelated failure modes:

### 1. Bad gold-chunk citations (the dominant cause, newly discovered by this gate)

Spot-checking refusals across **enrollment_count, primary_outcome_measure, arm_label, and sponsor**
found the "gold chunk" is frequently unrelated prose that has nothing to do with the question:

- **Enrollment "73" for NCT03008187**: the cited chunk is a table-of-contents listing
  ("3.3. DEFINITION OF EVALUABLE PATIENT ... 41  3.4. REASONS FOR WITHDRAWAL ... 42 ..." with no
  actual enrollment statement anywhere in it. "73" almost certainly matched a page/section number
  elsewhere in the same chunk, not a real enrollment figure. **The model's refusal here is correct
  behavior** given what it was actually shown.
- **Primary outcome for NCT03032484** (gold: "Progression Free Survival at 6 Months (PFS6)"): the
  cited chunk is background biology text about brain-tumor vascularization -- nowhere near an
  outcomes section.
- **Arm 1 label for NCT03088813** (gold: "Part 1: Experimental Arm, dose level 1"): the cited chunk
  is sample-size/efficacy-endpoint text for "Part 2," not an arm-label statement.
- **Sponsor for NCT03067610** (gold: "University of Texas Southwestern Medical Center"): the cited
  chunk is an SAE-reporting-procedures section.

None of these four cited chunks contain their supposed answer in any readable form. Given a
genuinely irrelevant excerpt, `NOT_ANSWERABLE` is the *correct* response, not a model failure --
which reframes most of the 51 refusals (25.5% of the set) as a citation-quality signal, not a
generation-quality signal.

**Likely mechanism**: S3-03's `locate_gold_chunk` falls back to `rapidfuzz.fuzz.token_set_ratio` at
a threshold of 85 for answers ≥6 characters (`MIN_LENGTH_FOR_FUZZY_MATCH`). For long, generic
phrases built from common clinical-trial vocabulary ("Center," "Study," "Patients," "Primary,"
"Outcome"), token-set overlap against an unrelated but similarly-worded paragraph can cross that
threshold by coincidence, especially in a corpus where every document shares the same domain
vocabulary. This is a different failure mode than the short-numeric substring bug S3-03 already
found and partially fixed (`contains_as_whole_token`, `MAX_REASONABLE_GOLD_CHUNKS`) -- those fixes
target *exact* substring collisions, not *fuzzy*-match false positives on long phrases.

### 2. Genuine scoring-strictness artifacts (secondary cause)

Independent of citation quality, `exact_match_score`'s known limitations explain a real chunk of the
remaining wrong-but-not-refused answers:

- **Phase**: at least 5 of 8 inspected `phase` failures are Roman-numeral or sub-phase notation
  mismatches -- model says "Phase II" / "Phase IIa" / "Phase 2a" / "Phase 1/2," gold says "Phase 2."
  `exact_match_score` deliberately does not attempt Roman-numeral normalization (documented
  scope decision from S3-03); a smarter model would very likely produce the *same* "Phase II"
  phrasing, since that's how protocols actually write it, and would still fail this exact check.
- **primary_outcome_measure / arm_label**: these are long, free-text phrases where a *semantically
  correct* paraphrase (e.g., "Dose limiting toxicity for olaparib in combination with ramucirumab
  and the maximum tolerated dose" vs. gold "Dose Limiting Toxicity and Maximum Tolerated Dose of
  Olaparib (Phase I)") fails exact/normalized matching despite being substantively right. These
  templates are a poor fit for exact-match grading regardless of model quality -- they need judged
  scoring, the same way T2 is graded.

### What's left: genuine model capability signal

After accounting for both of the above, the **credible generation-quality signal** is narrower than
40% suggests: `min_age` (96%) and `max_age` (82.6%) -- short, unambiguous, single-value facts with
(apparently) reliable gold citations -- both clear the 80% bar easily. The model is not obviously
failing at the specific job of stating a short fact when the given context actually contains it.

## Recommendation

**Do not treat this as evidence the local `llama3.1:latest` 8B model is inadequate**, and do not act
on the sprint plan's cloud-escape-hatch option on the strength of this number alone. The gate's own
literal rule says <80% triggers considering it, but the diagnosis above shows the shortfall is
substantially a **T1 gold-chunk citation quality problem** (S3-03) and an **exact-match scoring fit
problem** (S3-07) -- neither of which a stronger generation model would fix, since a cloud model
shown the same wrong table-of-contents excerpt would refuse too, and a cloud model asked for an
outcome measure would still phrase it slightly differently from the registry's exact wording.

**Suggested next steps, in priority order** (not implemented as part of this gate -- flagged for a
follow-up task):
1. Re-examine S3-03's fuzzy-match path (`locate_gold_chunk`'s `rapidfuzz` fallback) for long-phrase
   false positives -- likely needs a stricter threshold, a minimum token-overlap requirement, or
   restricting fuzzy matching to same-sentence/same-paragraph windows rather than whole-chunk
   comparison.
2. Re-run this gate after that fix, before drawing a final conclusion about local-model adequacy.
3. Consider judged (not exact-match) scoring for `primary_outcome_measure` and `arm_label`
   specifically, since their gold answers are long free-text phrases structurally similar to T2's.

**Retroactive implication for S3-12**: `results/ablation.md`'s T1 correctness column
(0.050 → 0.115 → 0.140 → 0.345 → 0.410 across the five rungs) was computed with the same
`exact_match_score` against the same (now-suspect) T1 gold-chunk citations, run against *retrieved*
chunks rather than gold ones. Retrieval failing to find the right chunk and the right chunk being
mis-cited in the first place are entangled in that number -- it should not be read as a clean
generation-quality trend until the citation-quality issue above is investigated. The retrieval
metrics in that same report (Recall/Precision/MRR/nDCG) are unaffected: those are computed by simple
set membership against `gold_chunk_ids`, not by generation or exact-match scoring, so they don't
inherit this problem.
