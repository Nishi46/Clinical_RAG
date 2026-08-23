# Discrepancy definition

Working definition of "discrepancy" for the discrepancy-detection task (S4-05), grounded in the
outcome-switching literature and in the real registry fields confirmed during Sprint 0. This
supersedes an intuitive but under-specified notion of "the outcome changed" with a precise,
three-way comparison and an explicit boundary between a real divergence and a false positive.

## 1. Why a precise definition matters

Robinson & Goodman (the primary background paper, PMC4032105) found that **31.7% of registered
trials had their primary outcome text change** between first posting and current record — but when
they manually reviewed a subset, only **~8% were truly significant changes**; the rest were
typographical or semantic rewording of the same underlying outcome. Mathieu et al. (JAMA 2009)
independently found a comparable ~31% discrepancy rate between registered and *published* outcomes,
with a striking bias: where a discrepancy existed and its effect could be assessed, the published
(changed) version favored a statistically significant result 82.6% of the time.

Two things follow directly from this:

1. **A naive text-diff between two outcome fields will produce a false-positive rate around 4-in-5.**
   Normalization (S4-03) is not a nice-to-have — it is the difference between a credible tool and a
   noisy one. This is the literature-backed justification for why `sprint_plan.md` calls S4-03 "the
   hard one and the interesting one."
2. **When a real discrepancy exists, there is a documented direction of bias** (toward reporting the
   result that turned out significant). This is useful context for *why* discrepancy detection is a
   meaningful task, but it is not something this project measures or asserts about any individual
   trial — see the ethics stance in §4.

## 2. The three-way comparison

Confirmed field paths (from `scratch/field_paths.md`, S0-01):

| Source | Field path | Availability |
|---|---|---|
| Registered primary outcome, **first posted** | `protocolSection.outcomesModule.primaryOutcomes[]` on the version-0 snapshot, via the undocumented `api/int/studies/{nct}/history/0` endpoint | Confirmed working, but unofficial — re-verify before Sprint 4 |
| Registered primary outcome, **current** | `protocolSection.outcomesModule.primaryOutcomes[]` on the current record, `api/v2/studies/{nct}` | Documented, stable |
| **Protocol-stated** primary outcome | Free text inside the protocol PDF, typically in a "Study Objectives" / "Outcome Measures" section (heading varies by sponsor — see `scratch/corpus_assessment.md`) | Requires retrieval; not a structured field |
| **Results-reported** primary outcome | `resultsSection.outcomeMeasuresModule.outcomeMeasures[]` filtered to `type == "PRIMARY"` | Documented, stable, only present when `hasResults == true` |

This gives three distinct comparison pairs, not one:

| Pair | What it detects | Grading |
|---|---|---|
| **First-posted registry vs. current registry** | Outcome switching in the registry itself — the classic phenomenon from the literature | Normalized text match |
| **Current registry vs. protocol document** | Whether the posted protocol PDF's stated outcome agrees with what's currently registered — tests retrieval + normalization, not switching per se | Normalized text match + citation to protocol section |
| **Registry (first-posted, and/or current) vs. results-reported** | Whether what was promised (at registration) is what was actually reported — the outcome-switching-into-results question the literature is most concerned with | Normalized text match + citation |

A single trial can diverge on one pair and not another — e.g., the protocol may faithfully match the
current registry, while the current registry differs from what was first posted. Report each pair's
verdict separately; do not collapse them into one "discrepancy: yes/no" flag per trial.

## 3. Match / divergence / ambiguous

| Verdict | Criteria |
|---|---|
| **Match** | Same clinical construct, same population, same timeframe (or a timeframe difference attributable only to unit/format, e.g. "24 months" vs. "2 years") — semantically equivalent phrasing is a match, not a divergence. This is the normalization boundary S4-03 has to get right; per §1, most raw text differences fall here. |
| **Divergence** | Different clinical construct (e.g., overall survival → progression-free survival), different comparator arm, a materially different population, or a different primary-vs-secondary designation for the same measure. |
| **Ambiguous / needs human review** | Wording changed enough that automatic normalization can't confidently classify it either way, multiple primary outcomes present in one source and not the other (partial overlap), or the protocol section couldn't be reliably retrieved (retrieval failure, not evidence of divergence — do not default retrieval failure to "divergence"). |

Confirmed real example of a **Divergence** (from S0-01, `NCT02872116`, CheckMate-649):
- Version 0 (first posted): *"Overall survival (OS) of nivolumab + ipilimumab versus oxaliplatin +
  fluoropyrimidine in subjects with PD-L1 expressing tumors"*
- Version 76 (current): *"Overall Survival (OS) in Participants Treated With Nivolumab Plus
  Chemotherapy vs Chemotherapy With PD-L1 CPS ≥ 5"* + a second primary outcome (PFS) added
- This is a genuine divergence under the definition above: the comparator arm changed
  (ipilimumab-combination → chemotherapy-combination) and the population/threshold definition
  changed (any PD-L1 expression → CPS ≥ 5) — not merely a rewording of the same construct.

## 4. Ethics stance (also goes in the README, per `project_plan.md` §5)

- **This is a research-integrity tooling project, not a clinical tool and not a fraud detector.**
  The system detects *textual and structural divergence* between sources. It does not, and cannot,
  determine *why* a divergence exists.
- **Flagged divergences are candidates for human expert review, never accusations.** The literature
  itself acknowledges "many reasons for departures from the initial study protocol" — amendments,
  regulatory feedback, safety findings, and legitimate protocol refinement all produce registry
  changes that are not misconduct. Per §1, most raw differences are noise, not even a genuine
  divergence.
- **The documented reporting bias (§1) is population-level evidence from the literature, not a claim
  this tool makes about any single trial.** Never present a flagged trial-level divergence with
  language implying intent, bias, or wrongdoing — only report the structural fact ("current registry
  primary outcome differs from first-posted primary outcome in comparator arm and population
  threshold") with citations to all sources involved.
- **No sponsor-level aggregation that implies ranking or blame.** Aggregate rates across the corpus
  are fine; per-sponsor "worst offender" framing is not.
- All data is public federal record (ClinicalTrials.gov); no PHI is involved.

## 5. Open items carried into Sprint 4

- S4-03's normalization component should be validated against a small hand-labeled set of phrase
  pairs (per `sprint_plan.md`), and — per this literature review — should expect the large majority
  of raw registry-vs-registry differences to be non-divergent. A normalization layer that flags
  anywhere near 31.7% of trials as divergent is almost certainly under-normalizing; ~8% or lower is
  the literature's ballpark for genuine change, though that number came from a different set of
  therapeutic areas and should not be treated as a hard target, only a sanity check.
- The first-posted-vs-current comparison depends on the undocumented `api/int/.../history` endpoint
  (S0-01 finding) — re-verify it still works before building S4-01 on top of it, and have a fallback
  plan (e.g., a cached/archived snapshot strategy) if it disappears.
