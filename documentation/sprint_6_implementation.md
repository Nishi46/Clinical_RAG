# Sprint 6 — Implementation Breakdown

Atomic, ordered implementation steps for Sprint 6 (Release / E7, ~16h). Companion to `sprint_plan.md`.
This is a writing and packaging sprint, not a building one — every number that goes into the README,
blog post, and dataset release must already exist in `docs/`, `results/`, or Postgres from Sprints
1-5. **If a number isn't already sitting in a committed artifact, that's a signal to go back and
regenerate it via the existing `scripts/`/`make` targets, never to type it in by hand.**

**Do not start S6-01 until every box on the Sprint 5 exit checklist is checked.**

---

## S6-01 — README (3h)

Results table + trace viewer screenshot above the fold, method second, reproduction third — this
ordering is deliberate per `sprint_plan.md`: a reader (interviewer, hiring manager) should see the
headline number before any architecture explanation.

1. Pull the results table directly from `results/ablation.md` (S3-12) and `docs/discrepancy_eval.md`
   (S4-08) — reformat into the single top-level table shape `project_plan.md` §10 sketches (Config /
   Recall@10 / nDCG@10 / Correctness T1 / Faithfulness / Discrepancy P/R / p95) — this is a
   reformatting pass over existing numbers, not a new computation.
2. Take the trace-viewer screenshot from S5-02 (a real query, not a placeholder) and the Pareto chart
   from S5-06/`docs/assets/failure_pareto.png`; place both above the fold alongside the results table.
3. Write the "why this corpus" section reusing `project_plan.md` §1-3's framing (the SEC-saturation
   argument, the structured-answer-key insight) condensed to a few sentences — this project already
   has that pitch written, don't rewrite it from scratch.
4. Write the method section (architecture diagram from `project_plan.md` §6, retrieval ladder
   rungs, discrepancy detection design) — second, after the results, per the ordering rule.
5. Write the reproduction section: point to `make reproduce` (S6-02, write this section after S6-02
   exists so the commands are verified, not aspirational).
6. Include the ethics/limitations section from S6-06 (write S6-06 first if sequencing allows, or stub
   it here and fold in once S6-06 is done — either order is fine, but don't ship the README without it).
7. Include 2-3 resume-bullet-style headline sentences (draft version; S6-07 finalizes with exact
   numbers) near the top, matching the "headline README line" pattern already modeled in
   `project_plan.md` §3 and §13.
8. Spot check every number in the README against its source doc by hand once, end to end — this is
   the last line of defense against a stale number surviving a late pipeline fix.

**Done when:** the README's top section (results table, trace-viewer screenshot, Pareto chart)
requires no scrolling to reach, and every number in it traces to a committed `docs/`/`results/` file.

---

## S6-02 — `make reproduce` (2h)

1. Create `scripts/reproduce.py` (or a `make reproduce` target that chains existing `make` targets
   directly, whichever is less duplicative — likely the latter, since `corpus-report`,
   `ingestion-report`, `ablation`, and the discrepancy/failure-analysis scripts already exist as
   individual targets from Sprints 1, 2, 3, 4, 5).
2. Decide and document the reproduction boundary explicitly: full reproduction from a clean clone
   realistically means "re-run all reports and eval scoring against the frozen `data/` artifacts
   already committed or fetchable," not "re-download 200 trials' PDFs and re-run a multi-night
   ablation sweep from zero" — state this boundary in the README's reproduction section (S6-01) so
   "a stranger reproduces the headline table from a clean clone" (the sprint's acceptance criterion)
   has an honest, achievable definition.
3. Handle the data-availability problem: `data/` is gitignored (per S1-01's `.gitignore` entry) and
   `data/pdfs/`, `data/registry_snapshots/`, embeddings, and the Postgres DB are all necessary inputs
   — decide whether `make reproduce` (a) re-runs the full pipeline from S1-02's live API client
   (slow, and the registry may have drifted since the cohort was frozen — the exact risk S1-04's
   archive exists to avoid), or (b) restores from a published data snapshot/dump referenced in the
   README. Pick (b) if at all feasible given the $0 budget (e.g. a Postgres `pg_dump` + the
   `data/registry_snapshots/` archive published alongside the HuggingFace dataset in S6-05) — it's the
   only version of "reproduce" that doesn't risk hitting live-registry drift on a stranger's machine.
4. Implement `make reproduce`: apply schema → restore/seed data → regenerate `docs/corpus.md`,
   `docs/ingestion.md`, `results/ablation.md`, `docs/discrepancy_eval.md`, `docs/failure_analysis.md`
   → print a final summary diffed against the checked-in versions of those files.
5. Test it on a genuinely clean checkout (a fresh clone into a scratch directory, not just the working
   copy) — this is the one step in this task that must not be skipped or approximated, since "works on
   my machine with my existing venv and DB" is exactly the failure mode this target exists to prevent.
6. Document the exact clean-clone steps (Python version, `pip install -e ".[dev,retrieval,eval]"`,
   Postgres setup, `make reproduce`) in the README's reproduction section.

**Done when:** a genuinely fresh clone, following only the README's reproduction steps, produces
output matching the committed headline tables (within the explicitly-documented reproduction
boundary from step 2).

---

## S6-03 — Blog post (4h)

Lead with the discrepancy finding and the failure analysis, not the architecture — the same ordering
principle as the README, applied to a narrative format instead of a document with sections.

1. Open with the `NCT02872116`-style finding: state the measured discrepancy precision/recall from
   `docs/discrepancy_eval.md` and one concrete (properly-anonymized-per-S6-06) example of what a
   flagged divergence looks like, structural language only.
2. Second: the failure-analysis story from `docs/failure_analysis.md` — "X was N% of failures;
   intervention Y cut it to M% and lifted end-to-end correctness Z points," the exact sentence
   `sprint_plan.md` calls out as "the single most valuable sentence in the project."
3. Third: the retrieval ladder and its ablation results — Recall@10 across rungs, with the specific
   naive-vs-section-aware assessment-schedule table screenshot from `docs/ingestion.md` (S2-10) as the
   "free first failure mode" visual `project_plan.md` §8 recommends.
4. Fourth: judge calibration (κ from `docs/judge_calibration.md`) and normalization accuracy (from
   `docs/normalization.md`) as evidence the evals themselves were validated, not just run.
5. Close with limitations and the ethics stance, condensed from S6-06 — a blog post that states its
   own limits reads as more credible, not less.
6. Reuse the retro notes in `docs/retros/` written throughout the project (per the Definition of
   Done's "anything surprising written to `docs/retros/` the same day") as raw material for the
   narrative details and any genuine surprises worth including — this is the exact purpose those retro
   notes were collected for, per `sprint_plan.md`'s sprint-ritual note ("those notes become the blog
   post and your interview stories").
7. Have the ethics section reviewed against `discrepancy_definition.md` §4 line by line before
   publishing — no sponsor named in a way implying wrongdoing, no aggregation framed as a
   worst-offender ranking.

**Done when:** the post's first two sections are the discrepancy finding and the failure-analysis
delta, every number cites a `docs/`/`results/` source, and the ethics section passes a line-by-line
check against `discrepancy_definition.md` §4.

---

## S6-04 — 60-second demo GIF (1.5h, 🟡)

Depends on S5-03 (discrepancy report view) — if S5-03 was cut in favor of the JSON endpoint, either
cut this task too (it's transitively cut-listed) or record the GIF against the JSON response
rendered in a simple viewer/`curl | jq` pass, and say so.

1. Script a 60-second flow: ask a real T2 question, show the streamed cited answer (S5-01), then show
   a flagged discrepancy for a real trial (S5-03's view, or the JSON fallback) with its citations.
2. Record via a lightweight screen-capture tool, export as GIF (keep file size reasonable for a
   README embed — a few MB, not tens).
3. Place in `docs/assets/demo.gif`, embedded near the top of the README (S6-01) alongside the static
   trace-viewer screenshot.

**Done when (if not cut):** `docs/assets/demo.gif` shows a real answer-with-citations flow followed
by a real flagged discrepancy, embedded in the README.

---

## S6-05 — Release the eval set (HuggingFace) with a datasheet (3h, 🟡 — first item on the cut list)

1. Assemble the release bundle from already-frozen files: `data/eval/t1.jsonl`, `t2.jsonl`,
   `t3.jsonl` + `t3_gold_labels.jsonl` (merged), `t4.jsonl`, `adversarial.jsonl` (if S4-09 wasn't
   cut) — ~430 questions total per `project_plan.md` §7.1's count, with gold chunk IDs intact.
2. Strip anything that shouldn't be public: confirm every source is already public federal record
   (ClinicalTrials.gov registry data and posted PDFs, per the ethics stance already established) — no
   additional PII/PHI risk beyond what's already true of the source corpus, but do a final pass
   checking the hand-written T2/T3 question text itself doesn't leak anything beyond the source
   documents.
3. Write a datasheet (`datasheets.md`-style, per the standard ML-dataset-documentation format):
   motivation, composition (per-tier counts, source), collection process (auto-generated from
   registry vs. hand-written, per tier), preprocessing, intended use, and — importantly for this
   corpus — the same ethics/limitations framing as `discrepancy_definition.md` §4, since the T3/T4
   discrepancy-adjacent questions could otherwise be misread as an accusation dataset.
4. Publish to HuggingFace Datasets under the project's account; link from the README.
5. Confirm the published dataset round-trips: load it back via `datasets.load_dataset(...)` and spot
   check a few rows against the local `data/eval/*.jsonl` originals.

**Done when (if not cut):** the ~430-question eval set is live on HuggingFace with a complete
datasheet, and a fresh `load_dataset` call round-trips correctly.

---

## S6-06 — Ethics + limitations section (2h)

Not dependent on anything else in this sprint — can be written any time Sprint 6 starts, and should
be written early since S6-01 and S6-03 both fold it in.

1. Transplant and adapt `discrepancy_definition.md` §4 and `project_plan.md` §5 — both already state
   the core ethics stance in publication-ready language; this task is mostly assembling and trimming
   what's already written, not drafting from a blank page.
2. State explicitly: divergence ≠ misconduct; flagged trials are candidates for human review, never
   accusations; the documented reporting-bias finding from the literature (§1 of the definition doc)
   is population-level evidence, never a claim about any individual trial.
3. Write the limitations section with **≥4 honest weaknesses**, per the acceptance criteria — draw
   these from what the project actually found, not generic disclaimers:
   - small n on the discrepancy adjudication (40-60 trials, per S4-07's actual count)
   - single therapeutic area (oncology, per `scratch/therapeutic_area_decision.md`)
   - judge κ ceiling (whatever S3-08 actually measured, stated plainly even if imperfect)
   - OCR/scanned-page gap (2.69% page-level per `docs/corpus.md`, but non-zero, and S2-03's default
     path explicitly skips rather than OCRs those pages)
   - the unofficial, unstable `/api/int/` history endpoint dependency (per `field_paths.md` §4)
   - any hybrid local/hosted generation split adopted at gate S3-G1, if it was triggered
4. Confirm no sponsor is named anywhere in the README/blog/limitations in a way that implies
   wrongdoing — a final grep for sponsor names (`sponsor_name` values from Postgres `trials`) across
   every public-facing doc, checking each hit's surrounding context by hand.
5. Get this section into both the README (S6-01) and the blog post (S6-03) — write it once here,
   reuse verbatim in both rather than drifting two versions apart.

**Done when:** the limitations section names ≥4 concrete, project-specific weaknesses, the ethics
stance matches `discrepancy_definition.md` §4 exactly, and a sponsor-name grep across all public docs
turns up nothing implying wrongdoing.

---

## S6-07 — Resume bullets with final real numbers (0.5h)

1. Take the illustrative bullets from `project_plan.md` §13 as a template and replace every number
   with the actual measured value from this project's own `docs/`/`results/` artifacts: Recall@10
   before/after (from `results/ablation.md`), judge κ (from `docs/judge_calibration.md`), discrepancy
   precision and n (from `docs/discrepancy_eval.md`), the top failure-category before/after delta
   (from `docs/failure_analysis.md`).
2. Confirm every number matches its source doc exactly — this is a two-minute check, not optional,
   given these bullets are the artifact most likely to get quoted out of context later.
3. Save alongside the README (a short section, or a separate `docs/resume_bullets.md`) for easy reuse.

**Done when:** every resume bullet cites a real number that matches its source doc exactly, with no
placeholder or illustrative figures remaining.

---

## Sprint 6 acceptance criteria

- A stranger reproduces the headline table from a clean clone
- Ethics section explicitly states discrepancies are candidates for human review, not accusations,
  and that legitimate reasons for outcome changes exist
- Limitations names ≥4 honest weaknesses
- No sponsor is named in a way that implies wrongdoing

---

## Sprint 6 exit checklist

- [ ] README — results table + trace-viewer screenshot above the fold, method second, reproduction
      third, every number sourced from a committed doc
- [ ] `make reproduce` — verified on a genuinely fresh clone, reproduction boundary documented
- [ ] Blog post — leads with the discrepancy finding and failure-analysis delta, ethics section
      checked line-by-line against `discrepancy_definition.md` §4
- [ ] `docs/assets/demo.gif` present, or explicitly noted as cut
- [ ] HuggingFace eval-set release live with datasheet, or explicitly noted as cut (first cut-list item)
- [ ] Ethics + limitations section — ≥4 named weaknesses, identical text reused in README and blog,
      sponsor-name grep clean
- [ ] Resume bullets — every number verified against its source doc
- [ ] CI still green: ruff + mypy + pytest (integration/db tests excluded) on every push

This is the last sprint — once every box above is checked, the project matches the milestone table's
final line: *"Here's the repo, the eval set, and the writeup."*
