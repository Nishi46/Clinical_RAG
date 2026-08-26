# Sprint 4 — Implementation Breakdown

Atomic, ordered implementation steps for Sprint 4 (Cross-Source & Discrepancy Detection / E4, E5,
~30h). Companion to `sprint_plan.md`. This is the headline capability and the sprint with the most
human labeling — protect S4-07's time explicitly; it is the single easiest task on this list to let
overrun.

**Do not start S4-01 until every box on the Sprint 3 exit checklist is checked**, in particular gate
S3-G1's outcome (local model vs. hybrid free-tier split for generation) — S4-05's detector inherits
that decision rather than re-deciding it.

**Ground truth for this sprint is `documentation/discrepancy_definition.md` — read it before writing
any code here, not after.** It already establishes, from the outcome-switching literature
(Robinson & Goodman; Mathieu et al. 2009):
- The three-way comparison is **three separate pairwise verdicts** (first-posted-vs-current,
  current-vs-protocol, registry-vs-results), never collapsed into one yes/no flag per trial.
- Match / Divergence / Ambiguous, with retrieval failure explicitly **not** defaulting to Divergence.
- A naive text-diff produces a false-positive rate around 4-in-5 (31.7% raw change rate vs. ~8% true
  significant-change rate) — this is *why* S4-03 gets 5 hours and its own labeled set, not a nice-to-have.
- The confirmed `NCT02872116` (CheckMate-649) divergence example — comparator arm changed
  (ipilimumab-combination → chemotherapy-combination) and population threshold changed (any PD-L1
  expression → CPS ≥ 5) — is the project's canonical positive test fixture for S4-05/S4-08.
- The `.../api/int/studies/{id}/history` endpoint is unofficial and unstable (`field_paths.md` §4) —
  S4-01 step 1 re-verifies it before anything else in this sprint depends on it.

---

## S4-01 — Amendment history extraction (3h)

1. **Re-verify the history endpoint first**, per the risk flag carried since Sprint 0: fetch
   `.../history` and `.../history/0` for 2-3 known cohort trials (`NCT02872116` among them) and
   confirm the response shape (`changes[]`, `lastUpdateVersions{}`, `outcomesUpdateCount`) matches
   `field_paths.md` §4 exactly. If it has changed or 404s, stop and write a fallback plan (the
   archived `data/registry_snapshots/{nct_id}/history.json` from S1-04 as the only source of
   amendment history — this sprint then works only with what's already archived, no live re-fetch)
   before continuing.
2. Create `src/protocol_drift/discrepancy/__init__.py` and
   `src/protocol_drift/discrepancy/amendments.py`. Implement `outcome_amendment_events(nct_id, conn)
   -> list[AmendmentEvent]` reading Postgres `amendments` (S1-05's table: `version`, `date`,
   `modules_changed`) filtered to rows where `modules_changed` contains an outcomes-related label
   (e.g. `"Outcome Measures"`, per the confirmed `moduleLabels` vocabulary in `field_paths.md` §4) —
   this identifies *which* trials actually had an outcome-touching revision, the population S4-02's
   T4 questions are drawn from.
3. For a trial with outcome-touching revisions beyond version 0 and current, the archived snapshot
   set (S1-04 fetched only version 0 and the latest, "at minimum") may not cover an intermediate
   version needed to describe *what specifically changed and when* for T4. Implement
   `ensure_version_snapshot(nct_id, version, conn) -> Path` — checks
   `data/registry_snapshots/{nct_id}/versions/{version}.json` first; if absent, fetches it via
   `get_history_version` (S1-02's client) and writes it into the same archive directory, extending
   S1-04's archive incrementally rather than re-architecting it. Log every trial that required an
   extra fetch — this is a real, reportable gap in what Sprint 1 archived.
4. Implement `diff_outcome_text(v_before, v_after) -> OutcomeDiff` — pulls
   `protocolSection.outcomesModule.primaryOutcomes[]` from each version snapshot and produces a
   plain-text diff of `measure`/`timeFrame`/`description`, tagged with both version numbers and
   dates. This is a *raw* diff — no normalization judgment happens here; S4-03 does that later.
5. Implement `amendment_timeline(nct_id, conn) -> list[AmendmentEvent with OutcomeDiff]` — combines
   steps 2 and 4 into one per-trial chronological list: first-posted → each outcome-touching revision
   → current.
6. Write `data/discrepancy/amendment_timelines.json`: one entry per cohort trial with ≥1
   outcome-touching revision.
7. Spot-check `NCT02872116` by hand: confirm the timeline surfaces the comparator-arm and
   PD-L1-threshold change between version 0 and the current record (92 revisions total per
   `field_paths.md`'s example), matching the confirmed example in `discrepancy_definition.md` §3.
8. Write `tests/discrepancy/test_amendments.py` against the `NCT02872116` fixture already in
   `tests/fixtures/` (`NCT02872116_v0.json`, `NCT02872116_current.json`, `NCT02872116_history.json`):
   `diff_outcome_text` correctly flags the comparator-arm and threshold change as textually different.

**Done when:** `data/discrepancy/amendment_timelines.json` covers every cohort trial with an
outcome-touching revision, the history endpoint's live shape is reconfirmed (or a fallback is
documented), and the `NCT02872116` spot-check passes.

---

## S4-02 — T4 question set (2h)

1. Create `src/protocol_drift/eval/t4_questions.py`. Templates over S4-01's
   `amendment_timelines.json`: `"Was the primary outcome changed after first posting for {nct_id}?
   When, and to what?"`, `"How many revisions touched the primary outcome for {nct_id}?"`,
   `"What was the primary outcome as first registered for {nct_id}?"`.
2. Gold answers come straight from the timeline data (exact match grading, per `project_plan.md`
   §7.1's T4 spec) — no chunk-location step needed since these are registry-only facts, unlike T1
   which needed a PDF-located gold chunk.
3. Sample ~40 trials, weighted toward ones with an actual outcome-touching revision (a trial with
   zero such revisions still makes a valid "no, it was not changed" question — include a handful of
   these deliberately, since a system that only ever answers "yes it changed" is trivially gameable
   and that failure mode should be caught here, not first noticed in S4-08's false-positive count).
4. Write `data/eval/t4.jsonl`.
5. Write `tests/eval/test_t4_questions.py`: a fixture timeline with one known revision produces the
   expected "changed, from X to Y, at version N" gold answer text.

**Done when:** `data/eval/t4.jsonl` has ~40 amendment-aware questions, including both
changed-outcome and unchanged-outcome cases.

---

## S4-03 — Outcome normalization layer ⭐ (5h)

**The hard one and the interesting one** — per `sprint_plan.md`, and per the literature review in
`discrepancy_definition.md` §1, this single component determines whether the detector is credible or
noisy. Build it as a standalone, separately-tested unit; do not embed its logic inline inside S4-05.

1. Create `src/protocol_drift/normalize/outcome.py`. Define `NormalizedOutcome`: canonical
   `construct` (the clinical measure itself — "overall survival," "progression-free survival"),
   `population` (arm/comparator/threshold description), `timeframe` (converted to a canonical unit,
   e.g. months), `raw_text`.
2. Implement `normalize_timeframe(text) -> float | None` — regex + unit conversion covering the
   concrete example the definition doc already commits to ("24 months" ≡ "2 years"): parse
   `(\d+(\.\d+)?)\s*(day|week|month|year)s?` and convert to months as the canonical unit.
3. Implement `normalize_construct(text, judge_client) -> str` — for the clinical-construct and
   population/comparator fields, a regex/keyword pass alone won't cover the space (arbitrary
   phrasing of the same construct); use the local judge model (per `configs/models.yaml`, same one
   S3-08 calibrated) with a tightly-scoped prompt: given two outcome-measure text blocks, classify
   `{match, divergence, ambiguous}` per the exact rubric in `discrepancy_definition.md` §3 — reuse
   that rubric's wording verbatim in the prompt rather than re-deriving a new one.
4. Implement `compare_outcomes(a: str, b: str, judge_client) -> NormalizationVerdict` combining steps
   2-3: a timeframe-only difference that normalizes to the same value is `match` without needing a
   judge call at all (cheap, deterministic, and the literature's stated dominant false-positive
   source — handle it in code, not by spending a judge call on it every time).
5. **Build the small labeled validation set now, before wiring S4-05**: hand-construct ~100 phrase
   pairs — a mix of true matches (timeframe/unit rewording, minor phrasing), true divergences (the
   `NCT02872116` comparator-arm example, and a few synthetic ones), and ambiguous cases. Write to
   `data/normalization/phrase_pairs.jsonl` with hand-assigned labels.
6. Implement `evaluate_normalization(phrase_pairs, compare_outcomes) -> NormalizationReport` —
   accuracy against the hand-labeled set, reported **separately** from the eventual discrepancy P/R/F1
   (S4-08) so a reader can see normalization quality in isolation — per `sprint_plan.md`'s own framing,
   "that subcomponent is a blog post on its own."
7. Sanity-check against the literature ratio from `discrepancy_definition.md` §5: run
   `compare_outcomes` over every `registered_first` vs. `registered_current` pair in the full cohort
   (via Postgres `outcomes`) and report what fraction comes back `divergence`. If it's anywhere near
   31.7%, the normalizer is under-normalizing — revisit step 3's prompt before trusting it downstream;
   the literature's own manually-reviewed ~8% is a sanity check, not a hard target (their sample was a
   different therapeutic-area mix).
8. Write `docs/normalization.md`: accuracy on the 100-pair set, confusion matrix (match/divergence/
   ambiguous), and the corpus-wide divergence-fraction sanity check from step 7.
9. Write `tests/normalize/test_outcome.py`: the confirmed `NCT02872116` divergence example scores
   `divergence`; a synthetic "24 months" vs "2 years" pair scores `match` via the deterministic
   timeframe path with zero judge calls (mock the judge client and assert it's never invoked for this
   case).

**Done when:** `docs/normalization.md` reports accuracy against the 100-pair labeled set separately
from any downstream discrepancy metric, and the corpus-wide sanity check is within a defensible range
of the literature's ~8% ballpark (or the gap is explained, not hidden).

---

## S4-04 — Query decomposition (3h)

Ladder rung 6 — depends on S3-11's rerank-topped retrieval.

1. Create `src/protocol_drift/retrieval/decompose.py`. Implement `decompose_cross_source_query(query,
   nct_id) -> list[SubQuery]` — for a T3-style comparison question, split into per-source
   sub-retrievals: one targeting `doc_type='protocol'` (retrieve the endpoint statement from the
   `objectives` canonical section — S2-04's taxonomy already has this exact label), and implicitly
   the two registry-side legs, which don't need retrieval at all since they're structured Postgres
   `outcomes` rows (`registered_first`, `registered_current`) — decomposition here is mostly "don't
   try to retrieve what's already structured," not a heavy NLP split.
2. Implement `answer_cross_source_query(query, nct_id, embedder, conn, reranker) ->
   CrossSourceAnswer` — runs the protocol-side sub-query through S3-11's `rerank_ladder` prefiltered
   to `doc_type='protocol', section='objectives'`, pulls the two registry legs by a direct SQL lookup,
   and returns all three raw texts plus their provenance (chunk ID for the protocol leg, `source`
   value for the registry legs) — this is the direct input to S4-05, not yet a verdict.
3. Wrap the protocol-side retrieval sub-step in `traced_call(..., "rerank")` like every other
   retrieval call; log the registry-side lookups as their own lightweight `retrieval_step` rows with
   `stage` extended if needed (check whether `trace/schema.sql`'s `stage` CHECK constraint needs a
   new value like `'structured_lookup'` — if so, that's a one-line migration, not a schema redesign).
4. Handle the retrieval-failure case explicitly: if the `objectives` section is `unclassified` or
   empty for a given trial (expected on the thin academic-summary documents flagged in
   `corpus_assessment.md` §4), return `protocol_leg=None` rather than an empty string — S4-05 must be
   able to distinguish "we don't know" from "we looked and found nothing," per the definition doc's
   explicit rule that retrieval failure is not evidence of divergence.
5. Write `tests/retrieval/test_decompose.py`: a fixture query against a trial with a known
   `objectives` section retrieves the right protocol chunk; a trial with no `objectives` section
   returns `protocol_leg=None` without raising.

**Done when:** `answer_cross_source_query` returns all three legs' raw text (or an explicit `None`
for an unretrievable leg) for a given trial, fully traced.

---

## S4-05 — Discrepancy detector (4h)

The core deliverable. Depends on S4-03 (normalization) and S4-04 (decomposition).

1. Create `src/protocol_drift/discrepancy/detector.py`. Implement `detect_discrepancies(nct_id, conn,
   embedder, reranker) -> DiscrepancyReport` — calls S4-04's `answer_cross_source_query` for the
   trial's primary outcome, then runs S4-03's `compare_outcomes` on **each of the three pairs
   separately**, per `discrepancy_definition.md` §2's table:
   - first-posted registry vs. current registry (`registered_first` vs. `registered_current`, both
     already in Postgres — no retrieval needed for this pair)
   - current registry vs. protocol document (`registered_current` vs. the S4-04 protocol leg)
   - registry (first-posted and/or current) vs. results-reported (`results_reported`, only when
     `hasResults` — join against `trials.has_...`; note `outcomes.source='results_reported'` may be
     absent for a trial and that's expected, not an error)
2. `DiscrepancyReport` carries three independent `PairVerdict` objects (`match` / `divergence` /
   `ambiguous`, per §3), **never** a single collapsed flag — this is a direct, load-bearing
   requirement from the definition doc, not a style preference.
3. Every verdict carries citations to all sources it compared: for the registry-only pair, cite the
   two `outcomes` row IDs; for the protocol-comparison pair, cite the S4-04 protocol leg's chunk ID
   (or explicitly `None` with a `retrieval_failed=True` flag, distinguished from an `ambiguous`
   normalization verdict).
4. Implement `render_verdict_text(verdict) -> str` — plain structural language only, per the ethics
   stance in `discrepancy_definition.md` §4: "current registry primary outcome differs from
   first-posted primary outcome in comparator arm and population threshold" style, never language
   implying intent or wrongdoing. Write a unit test that greps the output for a small denylist of
   loaded words (`"fraud"`, `"lied"`, `"hid"`, `"cheat"`) and fails if any appear — a cheap, durable
   guardrail for a requirement that's easy to accidentally violate later while iterating on prompts.
5. Run `detect_discrepancies` across the full cohort; write `data/discrepancy/reports/{nct_id}.json`.
6. Spot-check `NCT02872116`: confirm the current-vs-protocol or first-posted-vs-current pair (whichever
   the retrieved protocol text supports) comes back `divergence`, matching the confirmed example.
7. Write `tests/discrepancy/test_detector.py`: the `NCT02872116` fixture produces a `divergence`
   verdict on the registry-only pair (no retrieval dependency, so this test needs no chunk fixtures);
   a synthetic trial with identical text across all three sources produces `match` on every pair.

**Done when:** every cohort trial with a locatable protocol endpoint has a `DiscrepancyReport` with
three independently-graded, citation-backed verdicts, and the denylist guardrail test passes.

---

## S4-06 — T3 question set (2.5h)

~60 cross-source comparison questions — depends on S4-05 existing so questions can be checked against
real detector output, but gold labels come from **S4-07's independent adjudication**, not from the
detector's own verdicts (that would be circular).

1. Create `src/protocol_drift/eval/t3_questions.py`. Templates: `"Does the protocol's stated primary
   endpoint for {nct_id} match the current registry record?"`, `"Does the current registry record for
   {nct_id} match what was first posted?"`, `"Do the reported results for {nct_id} match the
   registered primary outcome?"` — one template per pairwise comparison in §2's table.
2. Sample ~60 trials for T3, stratified to include: several with a known/likely divergence (from
   S4-01's amendment-touching set), several with clean matches, and a handful of retrieval-failure
   candidates (thin documents from `corpus_assessment.md` §4) so the eval set can measure the
   "ambiguous ≠ retrieval failure" distinction, not just accuracy on easy cases.
3. Write `data/eval/t3.jsonl`: `question_id`, `nct_id`, `pair` (which of the three comparisons),
   `question_text` — **no gold label yet**; S4-07 fills that in via blind adjudication, deliberately
   kept as a separate artifact so the file that holds questions and the file that holds labels are
   never the same file a script could accidentally cross-contaminate.
4. Write `tests/eval/test_t3_questions.py`: template rendering produces the expected question text
   per pair type.

**Done when:** `data/eval/t3.jsonl` has ~60 stratified cross-source questions with no gold label
attached yet.

---

## S4-07 — Hand-adjudication (5h)

**Will overrun if you let it — five hours of careful labeling is realistic, ten is not available.**
If behind schedule, cut to 40 trials and report the smaller n honestly (per the acceptance criteria
and the cut list's own item 6) rather than rushing 60.

1. Write the adjudication rubric **first**, as a standalone doc (`docs/adjudication_rubric.md`),
   directly operationalizing `discrepancy_definition.md` §3's match/divergence/ambiguous criteria
   into a labeling checklist a human can follow consistently trial-to-trial — commit this file before
   labeling a single trial.
2. Select 60 trials (the same T3 sample from S4-06, or a superset if adjudicating beyond exactly what
   T3 asks about is useful for S4-08's denominator).
3. **Label blind**: read only the protocol PDF and the two registry snapshots (current + version 0,
   plus results if present) directly — never the detector's `DiscrepancyReport` output, never S4-05's
   code. Per the risk register's **Critical**-impact risk, enforce this mechanically, not just by
   discipline: adjudicate before ever running `detect_discrepancies` on these specific 60 trials in
   this session, or adjudicate from a separate checkout/branch that has no access to
   `data/discrepancy/reports/`.
4. For each trial and each of the three pairwise comparisons, record a `match`/`divergence`/
   `ambiguous` label plus a one-sentence justification, into `data/eval/t3_gold_labels.jsonl` (kept
   deliberately separate from `t3.jsonl`, per S4-06 step 3).
5. Track time explicitly per trial (a simple running log is enough) — at the 5-hour mark, stop, count
   what's labeled, and if short of 60, cut to whatever whole number is labeled rather than rushing the
   remainder; update the sprint's task list to reflect the actual n.
6. Only after all labeling is committed: run `detect_discrepancies` (already built in S4-05) over the
   same trial set and diff against the labels for a first look — this is the transition into S4-08,
   not part of adjudication itself.
7. Write a short retro note in `docs/retros/` the same day per the project's Definition of Done —
   record the actual n, actual hours spent, and anything that made a case genuinely ambiguous even
   under the rubric (this is itself analysis material for S5's failure taxonomy).

**Done when:** `data/eval/t3_gold_labels.jsonl` has labels for 40-60 trials (honestly reported n),
`docs/adjudication_rubric.md` was written and committed before labeling started, and no detector
output was consulted during labeling.

---

## S4-08 — Discrepancy scorer (2h)

1. Create `src/protocol_drift/eval/discrepancy_scorer.py`. Implement `score_discrepancy_detection(
   gold_labels, detector_reports) -> DiscrepancyScores` — precision/recall/F1 computed **per pair
   type** (first-posted-vs-current, current-vs-protocol, registry-vs-results) and also pooled, since
   `discrepancy_definition.md` treats them as genuinely distinct signals, not one task.
2. Treat `divergence` as the positive class; decide explicitly (and document the choice) how
   `ambiguous` is scored — the definition doc's own framing suggests `ambiguous` should count as
   neither a clean true-positive nor false-positive without human review, so report it as its own
   bucket (e.g. "N cases both graded ambiguous," "N cases detector said match/divergence but human
   said ambiguous") rather than silently coercing it into a binary.
3. Implement a confidence interval for precision/recall given the small n (40-60): a Wilson score
   interval is a reasonable, dependency-light choice (`scipy.stats` if already available via the
   `eval` extra from S3-08, otherwise a direct closed-form implementation — no need for a bootstrap
   given the sample size and the time budget).
4. Report precision prominently and first, per `project_plan.md` §7.2 — "a false discrepancy
   accusation is far more costly than a miss."
5. Write `docs/discrepancy_eval.md`: P/R/F1 per pair type with CIs, the ambiguous-bucket breakdown,
   and the actual n from S4-07 stated explicitly ("n=60" or the honestly-reduced number).
6. Write `tests/eval/test_discrepancy_scorer.py`: a small hand-constructed gold/predicted label set
   produces hand-computed P/R/F1; the Wilson interval matches a known reference value for a simple
   case (e.g. 8/10 correct).

**Done when:** `docs/discrepancy_eval.md` reports P/R/F1 with confidence intervals, per pair type,
against the actual adjudicated n.

---

## S4-09 — Adversarial set + refusal metrics (2h, 🟡 cuttable)

First on the sprint's own priority list to drop if time runs out (cut-list item 3) — do S4-01 through
S4-08 first and only pick this up if there's time left in the sprint.

1. Create `src/protocol_drift/eval/adversarial_questions.py`. ~30 questions genuinely unanswerable
   from the corpus: questions about documents that were never posted for a given trial (e.g. asking
   about SAP content for a `has_sap=False` trial), questions about facts no protocol contains (per
   `project_plan.md` §7.1's example category).
2. Write `data/eval/adversarial.jsonl` — no gold answer, just `expected_behavior="refuse"`.
3. Implement `refusal_metrics(questions, generate_fn) -> RefusalScores` — refusal accuracy (correctly
   says `NOT_ANSWERABLE`, per S3-06's explicit refusal instruction) on the adversarial set, and
   over-refusal rate (incorrectly refuses) measured against a sample of already-answerable T1/T2
   questions from Sprint 3.
4. Write `docs/refusal_eval.md`: refusal accuracy and over-refusal rate.
5. Write `tests/eval/test_adversarial.py`: refusal detection correctly parses `NOT_ANSWERABLE`
   verbatim vs. a hedge that isn't a clean refusal (e.g. "I'm not sure, but possibly X" should not
   count as a refusal).

**Done when (if not cut):** `docs/refusal_eval.md` reports refusal accuracy on ~30 adversarial
questions and an over-refusal rate on a sample of answerable ones.

---

## Sprint 4 acceptance criteria

- Adjudication rubric written before labeling, committed, and followed
- Labeling done blind to system output — otherwise the ground truth is contaminated
- Discrepancy P/R/F1 reported with confidence intervals (n=60 is small; say so)
- Every flagged discrepancy carries citations to all three sources

---

## Sprint 4 exit checklist

- [ ] History endpoint re-verified live (or fallback-to-archive-only documented) before any other
      task in this sprint depended on it
- [ ] `data/discrepancy/amendment_timelines.json` — every outcome-touching cohort trial, `NCT02872116`
      spot-check passes
- [ ] `data/eval/t4.jsonl` — ~40 amendment-aware questions, including unchanged-outcome cases
- [ ] `docs/normalization.md` — accuracy on 100 hand-labeled phrase pairs, reported separately from
      discrepancy P/R/F1; corpus-wide divergence-fraction sanity check against the ~8% literature
      ballpark
- [ ] `data/discrepancy/reports/` — three independently-graded, citation-backed verdicts per trial;
      denylist-guardrail test green
- [ ] `data/eval/t3_gold_labels.jsonl` — 40-60 blind-adjudicated trials, rubric committed before
      labeling began
- [ ] `docs/discrepancy_eval.md` — P/R/F1 with confidence intervals, per pair type, actual n stated
- [ ] `docs/refusal_eval.md` — present if S4-09 wasn't cut; explicitly noted as cut if it was
- [ ] CI still green: ruff + mypy + pytest (integration/db tests excluded) on every push

Do not start Sprint 5's serving/failure-analysis work until every box above is checked — S5-05's
failure labeling and S5-06's Pareto chart both draw on this sprint's ablation and discrepancy results
as their raw material.
