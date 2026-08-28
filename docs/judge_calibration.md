# Judge calibration (S3-08)

50 T2 responses, hand-labeled against `docs/judge_rubric.md` (written *before* any generation or judge-scoring happened) and independently scored by `judged_correctness` (S3-07), compared via linear-weighted Cohen's kappa.

**Setup**: retrieval isn't built yet (S3-09 through S3-11), so each question was fed its own real hand-verified gold chunks (`gold_chunk_ids`) directly as context, the same "skip retrieval, isolate generation" design S3-G1 uses for T1 right after this gate. 50 of the 101 T2 questions were sampled with a fixed seed (`random.Random(42).sample`, seed chosen for no reason other than reproducibility) so this run can be repeated exactly.

## Result

**κ = 0.380** (linear-weighted Cohen's kappa, n=50) — below the 0.6 target.

Per the task's own 3-hour budget and 2-iteration cap, this is the accepted, final, honestly-reported number: the judge prompt was revised twice (below), the better of the two revisions was kept, and iteration stopped there rather than continuing indefinitely in search of a higher κ.

**What this means in practice**: `judged_correctness` should not be trusted as a silent ground truth for T2 — a κ this low (below the conventional "moderate agreement" threshold of 0.6, and only just above "fair") means a meaningful fraction of its scores disagree with careful human judgment, in both directions (the confusion matrix below shows the judge is neither uniformly harsher nor uniformly more lenient — it disagrees inconsistently). Any T2 result reported downstream should note this explicitly rather than presenting judge scores as ground truth, and a spot-check of judge-scored disagreements against the actual answer text remains necessary — exactly the discipline `project_plan.md` §7.3 asks for ("two sentences about judge calibration puts you ahead of nearly every candidate, because it shows you know the eval is itself a system that can be wrong").

## Revision history

| Version | κ | Change | Outcome |
|---|---|---|---|
| v1 | 0.202 | Original one-line rubric ("1 = fully correct, 0.5 = partially correct, 0 = incorrect") | Judge penalized phrasing/unit differences as factual errors, and in several cases cited an omission or error that, on inspection of the actual answer text, was not really there (e.g. claimed "heterogeneously dense" was omitted when it was present verbatim). |
| v2 | **0.380** | Explicit "grade substance not wording", spelled-out 0/0.5/1 definitions matching the written rubric, and required the justification to quote the exact answer text for any deduction | Best of the three; kept and shipped. |
| v3 (tried, reverted) | 0.223 | Added "do not invent a scope/timing/implication distinction not explicitly in the reference notes", targeting a new failure pattern v2 surfaced (the judge inventing hairsplitting distinctions, e.g. quoting identical text from both sources as if it contradicted itself) | Made agreement *worse*, not better -- a real, honest result that further prompt tweaking on an 8B local model doesn't monotonically improve calibration. Reverted; v2's prompt is what `correctness_scorer.py` ships with. |

Per the task's 2-iteration cap, iteration stopped after v3 rather than continuing to chase a higher κ — the honest, reported result is v2's 0.380.

## Confusion matrix (human vs. judge, v2 prompt)

Rows = human label, columns = judge label.

| Human \ Judge | 0 | 0.5 | 1 | Row total |
|---|---|---|---|---|
| **0** | 3 | 0 | 0 | 3 |
| **0.5** | 3 | 5 | 3 | 11 |
| **1** | 3 | 10 | 23 | 36 |
| **Column total** | 9 | 15 | 26 | 50 |

Human label distribution: 0 → 3, 0.5 → 11, 1 → 36. Judge label distribution: 0 → 9, 0.5 → 15, 1 → 26.

The judge skews toward 0 relative to the human labels (16 human-1 answers judged 0 or 0.5 combined, per the row-3 breakdown above), consistent with the disagreement pattern in the examples table: the judge frequently treats a correctly-substantive but loosely-phrased answer, or an answer with a plausible unverified elaboration, as wrong.

## All 50 examples

`gold_answer_notes` is the human labeler's reference (from `data/eval/t2.jsonl`); `generated_response` is the real model output the human and judge both scored.

### 1. `NCT03022032:t2:0`

**Question**: Why are patients with a life expectancy of 6 months or less excluded from this study?

**Reference notes**: 'patients with a life expectancy of ≤6 months, as determined by their oncology providers, will be excluded since they cannot participate in all of the required data collection.'

**Generated answer**: [4.0 RESEARCH SUBJECT ENTRY | 4.11 Screening & Recruitment]
Patients who are currently actively tracking their steps using wearable technology or smartphone apps will also be excluded. 

There is no direct mention of life expectancy in this excerpt but the following excerpt does:

 [1]
3.2 Exclusion Criteria ... In addition, patients with a life expectancy of ≤6 months, as determined by their oncology providers, will be excluded since they cannot participate in all of the required data collection.

Patients with a life expectancy of 6 months or less are excluded because "they cannot participate in all of the required data collection" [1].

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Correctly states the exclusion reason (cannot participate in required data collection) after some rambling; final quoted answer is complete and accurate. |
| Judge | **0.5** | The answer states the reason for excluding patients with a life expectancy of 6 months or less as "they cannot participate in all of the required data collection", but this is only partially correct, as it omits the qualifying condition that the patient's life expectancy must be ≤6 months and determined by their oncology providers. |

### 2. `NCT03076190:t2:0`

**Question**: At what post-surgical time points are participants in this pre-surgical psychological intervention study asked to complete follow-up assessments of mood, pain, catastrophizing, skills use, medication use, and pain self-efficacy?

**Reference notes**: 'Patients may also be asked to complete over the phone or online assessments... of mood, pain, catastrophizing, skills use, medication use, and pain self-efficacy after surgery at 2,4,8,& 12 weeks post-surgery. ... Follow-up may be extended or modified, at the discretion of the investigator, beyond 3 months from the date of surgery.'

**Generated answer**: [1] At 2,4,8,& 12 weeks post-surgery.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Exact match to gold timepoints (2,4,8,12 weeks). |
| Judge | **1.0** | The answer accurately lists all four time points specified in the reference notes (2, 4, 8, and 12 weeks post-surgery) without omitting any information or introducing incorrect details. |

### 3. `NCT03011372:t2:1`

**Question**: Under the hyperphosphatemia dose-modification guidance for pemigatinib, what serum phosphate threshold and duration trigger drug interruption, and what must happen for the drug to be permanently discontinued?

**Reference notes**: Quote from Table 3 (Recommended Approach for Hyperphosphatemia Management), '>10 mg/dL' row: 'If serum phosphate level is > 10 mg/dL for 1 week following phosphate-binding therapy and low phosphate diet, interrupt study drug. If there is recurrence of serum phosphate level in this range following 2 dose reductions, permanently discontinue pemigatinib.'

**Generated answer**: [3]
> 10 mg/dL | ... | If serum phosphate level is > 10 mg/dL for 1 week following phosphate-binding therapy and low phosphate diet, interrupt study drug. If there is recurrence of serum phosphate level in this range following 2 dose reductions, permanently discontinue pemigatinib.

To be permanently discontinued, the serum phosphate level must be at or above 10 mg/dL for one week after starting phosphate-binding therapy and a low-phosphate diet, and there must be two dose reductions.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Both the interruption threshold and the permanent-discontinuation condition are stated correctly and completely. |
| Judge | **1.0** | The answer accurately states all necessary details for drug interruption and permanent discontinuation according to the reference notes. |

### 4. `NCT03042910:t2:1`

**Question**: Who is authorized to request permanent treatment discontinuation for 'Major noncompliance with protocol,' and under what conditions?

**Reference notes**: Literal row: 'Major noncompliance with protocol | The medical monitor or investigator may request permanent treatment discontinuation in the event of a major protocol deviation, lack of cooperation, or noncompliance.' Same source table as above.

**Generated answer**: [1]

The authorized personnel to request permanent treatment discontinuation for 'Major noncompliance with protocol' are the medical monitor or investigator. They may request this under the conditions of a major protocol deviation, lack of cooperation, or noncompliance.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Matches gold exactly: medical monitor or investigator, correct trigger conditions. |
| Judge | **1.0** | The answer accurately identifies who can request permanent treatment discontinuation (the medical monitor or investigator) and under what conditions (major protocol deviation, lack of cooperation, or noncompliance). |

### 5. `NCT03023423:t2:2`

**Question**: A patient on this trial is currently receiving denosumab for bone metastases — can they continue it while enrolled?

**Reference notes**: 'Denosumab; subjects who are receiving denosumab prior to enrollment must be willing and eligible to receive a bisphosphonate instead while in the study.'

**Generated answer**: [1]

According to Section 8.2 of the protocol, "Use of the treatments listed below is prohibited during the study..." and denosumab is one of the treatments listed in this section. The text states that subjects who are receiving denosumab prior to enrollment must be willing and eligible to receive a bisphosphonate instead while in the study.

So, based on this information, a patient cannot continue to receive denosumab while enrolled in the trial, but they would need to switch to a bisphosphonate.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Correctly concludes patient must switch to bisphosphonate, cannot continue denosumab. |
| Judge | **1.0** | The answer correctly states that patients on denosumab prior to enrollment need to switch to a bisphosphonate while in the study and accurately cites the relevant protocol section. |

### 6. `NCT03010176:t2:3`

**Question**: If a subject drops out of the DLT evaluation period within 21 days of the first dose, can they be replaced with a new subject, and does it matter whether they had a DLT?

**Reference notes**: 'Subjects discontinuing within 21 days of the first dose due to reasons unrelated to study treatment will not be considered evaluable for DLTs and may be replaced. Subjects with a DLT within 21 days of the first dose should not be replaced.'

**Generated answer**: According to Excerpt 1:

"Subjects discontinuing within 21 days of the first dose due to reasons unrelated to study treatment will not be considered evaluable for DLTs and may be replaced. Subjects with a DLT within 21 days of the first dose should not be replaced."

[1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Quotes the gold text verbatim and completely. |
| Judge | **0.0** | The answer contradicts the REFERENCE NOTES by stating that subjects with a DLT within 21 days of the first dose "should not be replaced", whereas the correct statement from the notes says they "should not be replaced." |

### 7. `NCT03010176:t2:0`

**Question**: Under the DLT definitions for this MK-1454 trial, when does thrombocytopenia count as a dose-limiting toxicity?

**Reference notes**: 'a.Grade 4 thrombocytopenia of any duration b. Grade 3 thrombocytopenia is a DLT if associated with clinically significant bleeding'

**Generated answer**: [1]

Thrombocytopenia counts as a dose-limiting toxicity when it meets one of two conditions:

* Grade 4 thrombocytopenia of any duration [1]
* Grade 3 thrombocytopenia if associated with clinically significant bleeding [1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Both DLT conditions (Grade 4 any duration; Grade 3 with bleeding) stated correctly. |
| Judge | **1.0** | The answer accurately states both conditions under which thrombocytopenia counts as a dose-limiting toxicity according to the DLT definitions for this MK-1454 trial. |

### 8. `NCT03007953:t2:1`

**Question**: Under what circumstances can the DSMB recommend early termination of this trial?

**Reference notes**: 'If at any time during the course of the study, the DSMB judges that risk to subjects may significantly outweigh the potential benefit, the DSMB shall have the discretion and responsibility to request all necessary information for detailed analyses, and if warranted, recommend that the study be terminated...'

**Generated answer**: [1]

The DSMB may request to terminate the study if:

* A significant number of adverse events can reasonably be attributed to participation in the study.
* The inability to recruit and measure the required number of participants to conduct the primary outcome analyses.
* Serious deviation from study protocols.
* Other circumstances that would render the study unlikely to produce scientifically valid findings.

[1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | Lists all 4 specific reasons correctly but omits the overarching standard (DSMB judges risk significantly outweighs benefit) that frames when those reasons apply. |
| Judge | **1.0** | The answer accurately summarizes the circumstances under which the DSMB can recommend early termination of the trial, as stated in the reference notes. |

### 9. `NCT03019055:t2:1`

**Question**: What specific toxicities from preceding lymphodepleting/cytoreductive chemotherapy will cause a delay of the CAR-20/19-T cell infusion, and what happens if the patient cannot receive the infusion within 7 days of completing chemotherapy?

**Reference notes**: 'The specific toxicities warranting delay of T cell infusions include: 1) Pulmonary... 2) Cardiac... 3) Hypotension... 4) Active Infection... Patients who are unable to receive the CAR-20/19-T cells within 7 days of completion of chemotherapy will be removed from the protocol.'

**Generated answer**: According to excerpt [1], specific toxicities from preceding cytoreductive chemotherapy that will cause a delay of the CAR-20/19-T cell infusion include:

* Pulmonary: Requirement for supplemental oxygen >2L by nasal cannula to keep saturation greater than 95% or presence of radiographic abnormalities on chest x-ray that are progressive;
* Cardiac: New cardiac arrhythmia not controlled with medical management
* Hypotension: requiring pressor support
* Active Infection: Fever>101 F and/or positive cultures for bacteria, fungus, or virus.

If the patient cannot receive the infusion within 7 days of completing chemotherapy, they will be removed from the protocol. [1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | All 4 toxicity categories and the 7-day removal rule stated correctly and completely. |
| Judge | **1.0** | The answer correctly identifies all specific toxicities that will cause a delay of the CAR-20/19-T cell infusion and accurately states what happens if the patient cannot receive the infusion within 7 days of completing chemotherapy, mirroring the REFERENCE NOTES exactly in terms of content and detail. |

### 10. `NCT03029286:t2:0`

**Question**: What combination of 5-year breast cancer risk score and mammographic breast density qualifies a woman for the randomized controlled trial arm of this study?

**Reference notes**: 'Utilizing the Breast Cancer Surveillance Consortium Risk Calculator..., women will have either (a) an intermediate 5-year risk (>1.67%-2.49%) and extremely dense breasts or (b) a high 5-year risk (≥2.50%) and either heterogeneously dense or extremely dense breasts.'

**Generated answer**: [2]

According to this excerpt, women will be eligible for the randomized controlled trial arm of this study if they have either:

(a) an intermediate 5-year risk (>1.67%-2.49%) and extremely dense breasts 
or
(b) a high 5-year risk (≥2.50%) and either heterogeneously dense or extremely dense breasts.

[2]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Exact match to the two-part risk/density eligibility criteria. |
| Judge | **1.0** | The answer accurately states both conditions (a) and (b) for eligibility in the trial arm, matching the description in the REFERENCE NOTES. |

### 11. `NCT03016312:t2:3`

**Question**: How long should the first atezolizumab infusion be given over, and how does the infusion duration for later doses depend on whether the patient had a prior infusion-related reaction?

**Reference notes**: Table 2: 'Atezolizumab should be infused over 60 (±15) minutes' for the first infusion; subsequently '30 (±10) minutes if the previous infusion was tolerated without an infusion-related reaction, or 60 (±15) minutes if the patient experienced an infusion-related reaction with the previous infusion.'

**Generated answer**: According to the protocol, for the first atezolizumab infusion, it should be administered over 60 (±15) minutes.

For subsequent doses, if the patient experienced an infusion-related reaction with any previous infusion, the duration is not specified in this excerpt. However, we do know that the infusion time depends on whether the patient had a prior infusion-related reaction: [1] "If clinically indicated, vital signs should be recorded during the infusion at 15, 30, 45, and 60 minutes (±5 minutes for all timepoints) during the infusion and at 30 (±10) minutes after the infusion."

However, further down in the excerpt we find that the infusion duration is dependent on whether the patient had an infusion-related reaction with the previous infusion: [1] "If the patient experienced an infusion-related reaction with the previous infusion or if clinically indicated, vital signs should be recorded during the infusion and at 30 (±5) minutes after the infusion."

So for subsequent doses, the infusion duration depends on whether the patient had a prior infusion-related reaction.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | First infusion duration (60±15 min) correct, but the second half of the question (duration depends on prior reaction: 30±10 vs 60±15) is never actually answered -- the model hedges, cites an unrelated vital-signs quote, and never states the real answer. |
| Judge | **0.0** | The answer contradicts the REFERENCE NOTES in stating that "the infusion time depends on whether the patient had a prior infusion-related reaction" for subsequent doses, when the actual information from the reference notes states that the duration should be either 30 (±10) minutes if tolerated or 60 (±15) minutes if an infusion-related reaction occurred. |

### 12. `NCT03102606:t2:1`

**Question**: How many docetaxel dose reductions are permitted over the course of this study, and by what percentage is the dose reduced if a critical adverse event occurs during a cycle?

**Reference notes**: 'If a critical AE occurs during the cycle, the dosage of docetaxel may be reduced 20% in the next cycle. Only one docetaxel dose reduction is allowed (refer to Taxotere® (Prescribing Information)). No dose reductions are allowed with plinabulin or pegfilgrastim.'

**Generated answer**: [1]
If a critical AE occurs during the cycle, the dosage of docetaxel may be reduced 20% in the next cycle.

Only one docetaxel dose reduction is allowed (refer to Taxotere® (Prescribing Information)). 

Therefore, only 1 docetaxel dose reduction is permitted over the course of this study, and the dose is reduced by 20%.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Both the reduction count (one) and percentage (20%) correct. |
| Judge | **0.0** | The answer contradicts the REFERENCE NOTES by stating that only one dose reduction is allowed over the course of the study, when it actually says "Only one docetaxel dose reduction is allowed" (singular), implying a single instance within the overall study, not across multiple cycles or the entire duration. |

### 13. `NCT03029234:t2:4`

**Question**: What laboratory criteria define tumor lysis syndrome (TLS) for purposes of carfilzomib dose-modification decisions in this study, and what action is taken if TLS is identified?

**Reference notes**: Table 3: 'Tumor Lysis Syndrome | 3 or more of the following: increase in creatinine of ≥ 50%... | Hold carfilzomib until all abnormalities in serum chemistries have resolved; resume at same dose'.

**Generated answer**: According to Excerpt 1, tumor lysis syndrome (TLS) is defined by three or more of the following laboratory criteria:

* Increase in creatinine of ≥ 50%
* Increase in uric acid of ≥ 50%
* Increase in phosphate of ≥ 50%
* Increase in potassium of ≥ 30%
* Decrease in calcium
OR
* Increase in LDH of ≥ 2-fold from baseline

If TLS is identified, the action taken is to hold carfilzomib until all abnormalities in serum chemistries have resolved; resume at same dose [1].

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Full TLS criteria list and the correct action, matching gold completely. |
| Judge | **1.0** | The answer accurately lists the laboratory criteria for tumor lysis syndrome and describes the appropriate action to take when TLS is identified, matching the information provided in the REFERENCE NOTES. |

### 14. `NCT03030417:t2:0`

**Question**: What duration and features of Grade 4 neutropenia count as a dose-limiting toxicity in this LMP744 phase I trial?

**Reference notes**: 'Grade 4 neutropenia for >5 days without fever or infection will be considered dose limiting. Grade 4 neutropenia of any duration accompanied by fever or infection will be considered dose limiting.'

**Generated answer**: Grade 4 neutropenia for >5 days without fever or infection will be considered dose limiting [1], and Grade 4 neutropenia of any duration accompanied by fever or infection will also be considered dose limiting. [1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Both duration/feature conditions for Grade 4 neutropenia DLT stated correctly. |
| Judge | **1.0** | The answer accurately states both conditions for dose-limiting toxicity due to Grade 4 neutropenia, matching the reference notes exactly. |

### 15. `NCT03052751:t2:0`

**Question**: At which visit is serology testing for HIV, Hepatitis B, and Hepatitis C performed in the Schedule of Assessments, and is it repeated later in the study?

**Reference notes**: Quote from Table 5.1 (Schedule of assessments): single X in the Screening column with every subsequent visit column blank. Confirmed consistently in a duplicate rendering of the same table (chunk 343): 'Serology testing for HIV, Hepatitis B, Hepatitis C, and active or latent TB' again only one X, at Screening.

**Generated answer**: [2]

Serology testing for HIV, Hepatitis B, and Hepatitis C is performed at Visit 4 (see "u p p o r t a" in excerpt [2]). 

It is repeated later in the study but only in excerpt [1], where it is stated to be done at Screening.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.0** | Garbled/incorrect: claims testing occurs at 'Visit 4' (not supported by gold, which says Screening only) and confusingly implies it is repeated, contradicting the gold answer that it is a one-time Screening-only test. |
| Judge | **0.0** | The ANSWER states that serology testing for HIV, Hepatitis B, and Hepatitis C is repeated later in the study at Screening (per excerpt [1]), which contradicts the REFERENCE NOTES indicating that this test is only performed once at Screening. |

### 16. `NCT03104543:t2:1`

**Question**: Which blood draws from the study's blood sample table are repeated at the 2nd home visit, and which are not (barring a failed first draw)?

**Reference notes**: Literal text: 'Draw order / Analyte ... 1) Lipid profile, glucose, insulin level* ... 2) Hemoglobin A1c* ...' and footnote '*These also will be drawn at the 2nd home visit; remaining tubes would NOT be drawn at the 2nd home visit unless they could not be successfully drawn at the first visit. 2nd home visit only: blood spot offered for failed venous draws.'

**Generated answer**: According to the table in excerpt [1], the following blood draws are repeated at the 2nd home visit:

* Lipid profile, glucose, insulin level*: This is marked with a "*" symbol and listed under "Draw order" as #1. The footnote (*) indicates that these will be drawn again at the 2nd home visit.
* Hemoglobin A1c*: Also marked with a "*" symbol and listed as #2. The footnote (*) indicates that this will also be drawn again at the 2nd home visit.

The remaining blood draws are not repeated unless a first draw is failed, except for the "blood spot" which can only be offered if a venous draw fails.

Therefore, the blood draws that are repeated at the 2nd home visit, barring a failed first draw, are:

* Lipid profile, glucose, insulin level (draw #1)
* Hemoglobin A1c (draw #2)

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Correctly identifies both repeated draws and correctly explains the non-repeated/backup-draw exception. |
| Judge | **0.5** | The ANSWER fails to mention the "blood spot" as a draw repeated at the 2nd home visit under normal circumstances (only mentioned as an option if the first venous draw fails), contrary to the REFERENCE NOTES which explicitly state that the blood draws listed are repeated except for those not marked with an asterisk, and the blood spot is only offered in case of failed first draw. |

### 17. `NCT03029286:t2:3`

**Question**: What safeguard is described to keep the participation incentive from being coercive, and at what reading level are the study materials written?

**Reference notes**: 'although we will be providing incentives for participation in the form of a gift card, the amount of the incentives are not so large as to be coercive. Study related materials will be written at an 8th grade level.'

**Generated answer**: The participation incentive is designed to be non-coercive because "the amount of the incentives are not so large as to be coercive." (See excerpt [1], 9.0).

Additionally, study-related materials will be written at an 8th grade level. (See excerpt [1], 11.0)

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Both the incentive-coercion point and the reading level stated correctly. |
| Judge | **1.0** | The answer accurately describes the safeguard to prevent coercive participation incentives and states the reading level of the study materials, both in accordance with the REFERENCE NOTES. |

### 18. `NCT03009344:t2:4`

**Question**: Per the statistical analysis plan, what time window defines a treatment-emergent adverse event (TEAE) relative to the last dose of study drug?

**Reference notes**: 'A treatment-emergent adverse event (TEAE) is defined as an AE that emerges during time from the first dose of study drug to 37 days after the subject's last dose, having been absent at pretreatment (Baseline) or Reemerges during treatment... or Worsens in severity during treatment...'

**Generated answer**: [1]

A treatment-emergent adverse event (TEAE) is defined as an AE that emerges during time from the first dose of study drug to 37 days after the subject’s last dose,, having been absent at pretreatment (Baseline) or • Reemerges during treatment, having been present at pretreatment (Baseline) but stopped before treatment, or • Worsens in severity during treatment relative to the pretreatment state, when the AE is continuous.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Verbatim match to the gold TEAE definition. |
| Judge | **0.5** | The answer states that a TEAE can "Reemerge during treatment, having been present at pretreatment (Baseline) but stopped before treatment", which contradicts the definition given in the REFERENCE NOTES; it should only be absent or reemerging during treatment. |

### 19. `NCT03010176:t2:1`

**Question**: How should a Grade 2 intratumoral injection site reaction (e.g., pain, lipodystrophy, edema) to MK-1454 be managed in terms of dose?

**Reference notes**: Table 12, Grade 2 row: 'Per medical assessment of the investigator: Consider holding dose until resolution to Grade 1 or baseline. Consider decreasing dose of IT MK-1454 to 450 µg and limit injections to a maximum of 2 lesions per study visit. If AE persists, discuss continuation with Sponsor.'

**Generated answer**: For a Grade 2 intratumoral injection site reaction (e.g., pain, lipodystrophy, edema) to MK-1454, according to [2], the investigator should "Consider decreasing dose of IT MK-1454 to 450 µg and limit injections to a maximum of 2 lesions per study visit."

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | Only mentions the dose-decrease option; omits the 'consider holding dose' option and the 'if AE persists, discuss with Sponsor' escalation path from the gold answer. |
| Judge | **0.5** | "Consider decreasing dose of IT MK-1454 to 450 µg and limit injections to a maximum of 2 lesions per study visit." - This answer is partially correct, but it omits the crucial first step mentioned in the REFERENCE NOTES to hold the dose until resolution to Grade 1 or baseline. |

### 20. `NCT03027557:t2:2`

**Question**: What are the exact doses and administration routes for denosumab, cinacalcet, and vitamin D in this trial?

**Reference notes**: 'The dose of the intervention preparations is 60 mg denosumab / half year, 30 mg cinacalcet / dgl, and for vitamin D 50 micrograms / dgl.' and 'administering prolia, the medicine is administered subcutaneously, which will take place once every six months'

**Generated answer**: According to the excerpts:

* Denosumab (Prolia®): 60 mg every 6 months, administered subcutaneously ([1])
* Cinacalcet (Mimpara®): 30 mg per day, administered orally ([1])
* Vitamin D: 50 micrograms per day, administered orally ([1])

Note that the administration routes are mentioned in [2] as well, but it is a repetition of what's already stated in [1].

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | All three drug doses and denosumab's route correct; oral route for the other two is a plausible unverified addition but not contradicted by gold. |
| Judge | **1.0** | The answer accurately provides the exact doses and administration routes for denosumab (60 mg every 6 months subcutaneously), cinacalcet (30 mg per day orally), and vitamin D (50 micrograms per day orally) as stated in the reference notes. |

### 21. `NCT03035032:t2:1`

**Question**: How recent must a patient's non-palliative radiotherapy have been to exclude them from this trial, and can this exclusion criterion be waived for an otherwise-interested patient?

**Reference notes**: '8. Patient receiving non-palliative radiotherapy within 3 months prior to study entry' and '...Waivers to the exclusion criteria will NOT be allowed.'

**Generated answer**: [8]

This exclusion criterion can be waived for an otherwise-interested patient. The text states that waivers to the exclusion criteria will NOT be allowed, but this specifically refers to the general rule of no waivers. However, in the case of non-palliative radiotherapy, it is stated "A patient where there is a likelihood to receive systemic chemotherapy should not be enrolled", and then lists specific exclusions. The question only asks about non-palliative radiotherapy, which is listed as exclusion criterion 8.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.0** | Gets the waiver question backwards -- claims the exclusion CAN be waived when gold explicitly states waivers are NOT allowed -- and never states the 3-month recency requirement at all. |
| Judge | **0.0** | The answer states "The text states that waivers to the exclusion criteria will NOT be allowed", which contradicts the REFERENCE NOTES that state "Waivers to the exclusion criteria will NOT be allowed" without specifying exceptions, and does not accurately describe the exception mentioned for non-palliative radiotherapy. |

### 22. `NCT03022032:t2:4`

**Question**: How often are the app-based symptom surveys administered to participants, and what happens if a participant misses one?

**Reference notes**: 'Symptom surveys are administered daily, and the participant cannot take the symptom survey more than once per day.' ... 'Missed surveys do not remain on the patient's action list and can be completed at the next survey timepoint, as applicable.'

**Generated answer**: Symptom surveys are administered daily, and a participant cannot take the symptom survey more than once per day. Missed surveys can be completed at the next survey timepoint.

[1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Matches gold exactly on frequency and the missed-survey behavior. |
| Judge | **0.5** | The answer correctly states that symptom surveys are administered daily and a participant cannot take the survey more than once per day, but it incorrectly implies that missed surveys will remain on the patient's action list ("a participant cannot take...") when in fact, according to the REFERENCE NOTES, "Missed surveys do not remain on the patient's action list". |

### 23. `NCT03029234:t2:0`

**Question**: On which cycle days must carfilzomib be infused with tighter timing precision than the usual infusion window, and what is that tighter window?

**Reference notes**: 'Carfilzomib is administered as an IV infusion over approximately 30 minutes (± 5 minutes). Note: On days of PK sampling (Cycle 1 Day 1 and Cycle 2 Day 1), carfilzomib must be administered over 30 ± 1 minutes.'

**Generated answer**: [1]

On days of PK sampling (Cycle 1 Day 1 and Cycle 2 Day 1), carfilzomib must be administered over 30 ± 1 minutes.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Correct days and correct tighter window. |
| Judge | **1.0** | The answer accurately states that carfilzomib must be administered with tighter timing precision on days of PK sampling (Cycle 1 Day 1 and Cycle 2 Day 1), specifically requiring it to be infused over a window of 30 ± 1 minutes, which is consistent with the reference notes. |

### 24. `NCT03009344:t2:2`

**Question**: Is a patient on this trial allowed to take corticosteroids for lymphoma-related symptoms, and if so, is there a dose limit?

**Reference notes**: 'Subjects may receive corticosteroid for local or systemic symptom control prior to and while on study. Subjects may receive no more than 10 mg of prednisolone daily or equivalent corticosteroid when used for treatment of lymphoma related symptoms.'

**Generated answer**: [1]

Yes, patients are allowed to take corticosteroids for lymphoma-related symptoms, and there is a dose limit of no more than 10 mg of prednisolone daily or equivalent corticosteroid.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Matches gold exactly, including the dose limit. |
| Judge | **1.0** | The answer accurately states that patients are allowed to take corticosteroids for lymphoma-related symptoms and correctly specifies a dose limit. |

### 25. `NCT03029286:t2:4`

**Question**: Which component of this study requires participants to attend in person, and which components do not?

**Reference notes**: 'While usability testing participants will need to attend in person, beta testing and trial participants will not be required to physically attend to participate.'

**Generated answer**: [1]

According to this excerpt, usability testing participants will need to attend in person [1], but beta testing and trial participants will not be required to physically attend to participate.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Matches gold exactly on which component requires in-person attendance. |
| Judge | **1.0** | The answer correctly identifies usability testing as requiring in-person attendance and accurately states that beta testing and trial participants do not need to attend in person. |

### 26. `NCT03045406:t2:3`

**Question**: What is the pre-specified non-inferiority margin for the primary efficacy hazard ratio in the Caravaggio SAP, and what sample size was calculated to detect it?

**Reference notes**: 'The criteria for non-inferiority require that the upper limit of the two-sided 95% confidence interval of the Hazard ratio is below the pre-specified margin of 2.00. ... we calculated that we would need to enroll 934 completer patients... increased to 1,168 patients to account for up to 20% lost...'

**Generated answer**: [9.3] The pre-specified non-inferiority margin for the primary efficacy hazard ratio is 2.00. To detect this margin, a sample size of 934 completer patients was calculated to have 80% power, which will be increased to 1,168 patients to account for up to 20% lost in total patient-years.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Margin and both sample-size figures (934 -> 1168) correct and complete. |
| Judge | **1.0** | The answer accurately states the pre-specified non-inferiority margin as 2.00 and correctly identifies the calculated sample size as 934 completer patients (later increased to 1,168 patients) with corresponding power level of 80%. |

### 27. `NCT03104543:t2:0`

**Question**: When are participants expected to complete the baseline questionnaires relative to their scheduled home visit?

**Reference notes**: Literal footnote from Table 3 (Summary of required observations): '*Participants will be asked to complete the questionnaires prior to the scheduling of their home visits.'

**Generated answer**: Participants are expected to complete the baseline questionnaires prior to the scheduling of their home visits. 

[1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Exact match to gold. |
| Judge | **0.5** | The core idea is correct, but the answer states "prior to the scheduling" which implies that the home visits have already been scheduled before completing questionnaires, whereas according to the reference notes, participants are asked to complete the questionnaires "prior to the scheduling" of their home visits, implying that the home visits themselves have not yet been scheduled. |

### 28. `NCT03030417:t2:3`

**Question**: How many dose reductions for DLT are patients allowed before being removed from this study, and what happens if a DLT recurs after those reductions?

**Reference notes**: 'If a patient is dose reduced twice and still experiences a DLT, then the patient would be removed from the study. If no lower dose level exists, then the patient will be removed from the study.'

**Generated answer**: [1]

According to section 6.2:

"If a patient experiences DLT during a cycle, the dose will be REDUCED by one level (if there is a lower dose level, see Section 5.1.2) for the next cycle... If a patient is dose reduced twice and still experiences a DLT, then the patient would be removed from the study."

This excerpt indicates that patients are allowed up to two dose reductions before being removed from the study, and if a DLT recurs after those reductions, they will be removed from the study.

Therefore, the answer is: 2 [1].

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Correctly derives the 2-reduction limit and the recurrence-triggers-removal rule; omits the secondary 'or no lower dose level exists' trigger, but the core scenario asked about is fully and correctly answered. |
| Judge | **1.0** | The answer accurately states that patients are allowed up to two dose reductions before being removed from the study and correctly identifies the consequence of a DLT recurrence after those reductions. |

### 29. `NCT03083041:t2:1`

**Question**: During post-treatment follow-up for time to progression, how often is imaging evaluation performed and until what event does it continue?

**Reference notes**: Literal cell: 'Time to Progression[25] | ... | Imaging evaluation will be carried out once every 3 months (± 7 d) until progressive disease or the initiation of other cancer treatments (subjects with non-imaging PD)'.

**Generated answer**: Imaging evaluation will be carried out once every 3 months (± 7 d) until progressive disease or the initiation of other cancer treatments (subjects with non-imaging PD) [25].

Additionally, it is mentioned that imaging examination will also be performed at End of treatment visit and every 2-3 cycles depending on the cycle number. However, the specific details about these are found in a different excerpt: Imaging Examination[24]. 

Therefore, considering both excerpts:

Imaging evaluation is performed once every 3 months (± 7 d) until progressive disease or the initiation of other cancer treatments, but it also continues at End of treatment visit and with varying frequency depending on cycle number [25][24].

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | States the correct core answer first, then conflates it with a different schedule item (Imaging Examination, a treatment-period assessment) from the same retrieved chunk, muddying what should be a single clean answer to a follow-up-specific question. |
| Judge | **1.0** | The answer accurately states the frequency and duration of imaging evaluation, including the specific conditions for its continuation. |

### 30. `NCT03029780:t2:0`

**Question**: During Part 1 dosing (Cycles 1-4, every 3 weeks), how often must a WOCBP participant have a pregnancy test, and within what window before each dose must a negative result be documented?

**Reference notes**: Quote from Table 2-2 (On-Study Assessments Part 1): 'Pregnancy Test (WOCBP only) | X | Serum or urine (minimum sensitivity equivalent units 25 IU/L or equivalent units of HCG) within 24 hours prior to administration of first dose and every 3 weeks thereafter in Part 1 of the study. A negative pregnancy test should be documented within 24 hours prior to start of each dose of investigational product.'

**Generated answer**: [1]

A WOCBP participant must have a pregnancy test every 3 weeks during Part 1 dosing (Cycles 1-4). A negative result must be documented within 24 hours before each dose. 

The required frequency and timing of the pregnancy tests is specified in section "Pregnancy Test (WOCBP only)" under "Cycle 1, 2, 3, 4", indicating that they should occur every 3 weeks.

[1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Correctly captures the every-3-weeks frequency and the 24-hour documentation window. |
| Judge | **0.5** | The answer states a correct requirement for pregnancy tests to be done every 3 weeks and within 24 hours before each dose, but it does not specify that a negative result must be documented "within 24 hours prior to start of each dose" as stated in the REFERENCE NOTES; however, this is a minor clarification on when the documentation should occur, which can be inferred from the requirement for tests to be done within 24 hours before each dose. |

### 31. `NCT03007407:t2:1`

**Question**: What happens if a patient has a Grade 3 or 4 infusion-related reaction to durvalumab or tremelimumab?

**Reference notes**: 'Grade 3 and 4: − Permanently discontinue study therapy. − Report event to DSSM. Manage severe infusion-related reactions per institutional standards (e.g., IM epinephrine, followed by IV diphenhydramine and ranitidine, and IV glucocorticoid).'

**Generated answer**: Permanently discontinue study therapy.

[1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | States only the discontinuation action; omits reporting to DSSM and the institutional-standards management detail entirely. |
| Judge | **0.5** | The answer lacks the additional necessary steps for managing severe infusion-related reactions (reporting to DSSM and specific medication administration). |

### 32. `NCT03029286:t2:1`

**Question**: Besides English-language ability, what personal cancer-history factors exclude a woman from the beta/usability testing or the randomized trial in this study?

**Reference notes**: 'Exclusion criteria for beta/usability testing and the trial include not able to speak and read English; history of LCIS, prior cancer diagnosis (including DCIS), known BRCA1/2 family mutation, or previous receipt of cancer genetic counseling.'

**Generated answer**: [1] Exclusion criteria for beta/usability testing and the trial include history of LCIS, prior cancer diagnosis (including DCIS), known BRCA1/2 family mutation, or previous receipt of cancer genetic counseling.

[1] Also, women who have had a prior cancer diagnosis (including DCIS) will be excluded.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | All four exclusion factors listed correctly despite a redundant repeat at the end. |
| Judge | **0.5** | The answer lists prior cancer diagnosis (including DCIS) twice and omits one of the exclusion criteria mentioned in the reference notes ("not able to speak and read English"). |

### 33. `NCT03015740:t2:1`

**Question**: Can a patient with a history of brain metastases enroll in this trial, and if so under what conditions?

**Reference notes**: 'Patients with controlled brain metastases are allowed on protocol if they had solitary brain metastases that was surgically resected or treated with radiosurgery or Gamma knife, without recurrence or edema for 1 month (4 weeks).'

**Generated answer**: [11] Patients with controlled brain metastases are allowed on protocol if they had solitary brain metastases that was surgically resected or treated with radiosurgery or Gamma knife, without recurrence or edema for 1 month (4 weeks).

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Exact quote match to gold. |
| Judge | **1.0** | The answer accurately states that patients with controlled brain metastases can enroll in the trial and specifies the conditions for eligibility, matching the reference notes exactly. |

### 34. `NCT03029234:t2:1`

**Question**: Is palliative radiation therapy for pain management allowed while a subject remains on study treatment, and if so, under what condition?

**Reference notes**: 'Palliative radiation for pain management is permitted with the written approval of the study medical monitor or designee.'

**Generated answer**: Palliative radiation therapy for pain management is allowed while a subject remains on study treatment if it has the written approval of the study medical monitor or designee.

[8.3.1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Matches gold exactly. |
| Judge | **1.0** | The answer accurately states that palliative radiation therapy for pain management is allowed while a subject remains on study treatment and specifies the condition (written approval of the study medical monitor or designee) as per the reference notes. |

### 35. `NCT03007407:t2:0`

**Question**: Are patients with pre-existing autoimmune conditions automatically excluded from this durvalumab/tremelimumab trial, or are there exceptions?

**Reference notes**: '4.2.20 Active or prior documented autoimmune disease or inflammatory condition requiring ongoing immunosuppressive medications. (Note: Patients with vitiligo, Grave disease, or psoriasis not requiring systemic treatment within the past 2 years are eligible.)'

**Generated answer**: [4.2.20] and [Note:] support this answer.

According to [4.2.20], patients with active or prior documented autoimmune disease are automatically excluded from the study if they require ongoing immunosuppressive medications. However, the note following that exemption mentions an exception: "Patients with vitiligo, Grave disease, or psoriasis not requiring systemic treatment within the past 2 years are eligible." This indicates that patients with pre-existing autoimmune conditions may be eligible for the trial under certain circumstances.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Correctly states both the general exclusion and the vitiligo/Grave's/psoriasis exception with the 2-year condition. |
| Judge | **1.0** | The answer accurately states that patients with pre-existing autoimmune conditions may be eligible for the trial under certain circumstances and provides a specific exception to the automatic exclusion. |

### 36. `NCT03022032:t2:2`

**Question**: In the second phase of this study, how are participants randomized across the study arms, and how many are assigned to each arm?

**Reference notes**: '100 patients will be enrolled to the second phase of this study, which will employ a 2x2 factorial design... Patients will be randomized 1:1:1:1 to one of the four arms.' Table: Fitbit+/Active=25, Fitbit+/Passive=25, Fitbit-/Active=25, Fitbit-/Passive=25, Total=100.

**Generated answer**: According to excerpt [2], in the second phase of this study, participants are randomized 1:1:1:1 across the four arms. Specifically:

* 25 patients will be assigned to Arm 1 (Fitbit + active app) [2]
* 25 patients will be assigned to Arm 2 (Fitbit + passive app) [2]
* 25 patients will be assigned to Arm 3 (Active app only, without Fitbit) [2]
* 25 patients will be assigned to Arm 4 (Passive app only, without Fitbit) [2]

These assignments are part of a 2x2 factorial design.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Captures the 2x2 factorial structure, 1:1:1:1 ratio, and correct per-arm counts (25 each), just with different arm labels than the gold table. |
| Judge | **1.0** | The answer accurately reflects all the requested information, including the randomization ratio and the number of patients assigned to each arm, matching the reference notes. |

### 37. `NCT03016312:t2:2`

**Question**: Under what circumstance is premedication allowed before an atezolizumab infusion in this study, and what type of premedication may be used?

**Reference notes**: Table 2: 'No premedication is permitted [first infusion].' ... 'If the patient experienced an infusion-related reaction with any previous infusion, premedication with antihistamines, antipyretics, and/or analgesics may be administered for subsequent doses at the discretion of the investigator.'

**Generated answer**: [1]

Premedication is allowed before an atezolizumab infusion if the patient experienced an infusion-related reaction with any previous infusion. The type of premedication that may be used includes antihistamines, antipyretics, and/or analgesics. [1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Correct circumstance and correct premedication types. |
| Judge | **0.5** | The answer contradicts the reference notes for the first infusion, stating "premedication is allowed" when it should say "no premedication is permitted." |

### 38. `NCT03028103:t2:1`

**Question**: On Study Day 1 of this PK sampling schedule, what is the timing window for the predose sample and what are the postdose collection timepoints?

**Reference notes**: Literal row from chunk: '1 | 2 mL | Predose (within 90 minutes prior to dose) and 0.25, 0.5, 1, 2, 3, 5, and 7 hours postdose'.

**Generated answer**: [1]
The timing window for the predose sample is within 90 minutes prior to dose [1].
The postdose collection timepoints are 0.25, 0.5, 1, 2, 3, 5, and 7 hours postdose [1].

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Exact match to gold timing window and postdose timepoints. |
| Judge | **1.0** | The answer accurately states the timing window for the predose sample and lists all the postdose collection timepoints as specified in the reference notes. |

### 39. `NCT03052751:t2:1`

**Question**: If a subject's IGRA (TB) test result at screening is indeterminate, what is the retest procedure, and what happens if the retest is also positive or indeterminate?

**Reference notes**: Quote (Section 12.3.1): 'If the result of the IGRA test is indeterminate, the particular IGRA test previously performed may be repeated once; if positive or indeterminate on retest, the subject must not be randomized to study drug and, if already randomized, must undergo appropriate study specified withdrawal procedures. The retest must be done during the protocol-defined Screening Period.'

**Generated answer**: If a subject's IGRA (TB) test result at screening is indeterminate, the retest procedure is to repeat the IGRA test previously performed once during the protocol-defined Screening Period. [1] If the retest result is also positive or indeterminate, the subject must not be randomized to study drug, and if already randomized, must undergo appropriate study-specified withdrawal procedures. [1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Complete and correct match to the retest procedure and consequence. |
| Judge | **1.0** | The answer accurately repeats the retest procedure and consequences for a positive or indeterminate result on the second test, mirroring the REFERENCE NOTES without any material omissions. |

### 40. `NCT03009344:t2:1`

**Question**: If a patient experiences a DLT during Cycle 0 or Cycle 1, at what dose can tazemetostat be resumed in Cycle 2?

**Reference notes**: 'If DLT occurs: Tazemetostat administration should be interrupted immediately. Treatment may be resumed in Cycle 2 at 600 mg BID (1200 mg total daily dose) if toxicity is resolved to Grade ≤ 1 or baseline and the investigator or subinvestigator decides to continue the study.'

**Generated answer**: [1] 

If a patient experiences a DLT during Cycle 0 or Cycle 1, tazemetostat can be resumed in Cycle 2 at a dose of 600 mg BID (1200 mg total daily dose). This is stated explicitly under the section "Tazemetostat Dose Reduction and Interruption Instructions" [1].

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | States the correct resumption dose but omits both conditions required for it (toxicity resolved to Grade<=1/baseline, and investigator decision to continue). |
| Judge | **0.5** | The answer states "the investigator or subinvestigator decides to continue the study" as a condition for resuming tazemetostat in Cycle 2 at 600 mg BID, which is not mentioned in the REFERENCE NOTES. |

### 41. `NCT03056755:t2:0`

**Question**: How often are chest/abdomen/pelvis CT or MRI tumor assessment scans required during treatment and follow-up, and what additional imaging requirement applies at End of Treatment if the most recent scan is outdated?

**Reference notes**: Quote from Table 7-3 (Imaging assessment collection guidance): 'Chest, abdomen and pelvis CT or MRI (with intravenous contrast enhancement) | Mandated | Mandated, every 12 weeks at EOT a tumor assessment scan should be performed if the previous scan is older than 30 days'.

**Generated answer**: According to the protocol, during treatment and follow-up, chest/abdomen/pelvis CT or MRI tumor assessment scans are mandated every 12 weeks.

[1]

Additionally, at End of Treatment (EOT), if the previous scan is older than 30 days, a tumor assessment scan should be performed. [1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Correct 12-week cadence and correct EOT >30-day rule; screening baseline scan not separately named but substantively covered. |
| Judge | **0.5** | The answer states that during treatment and follow-up, chest/abdomen/pelvis CT or MRI tumor assessment scans are mandated every 12 weeks, which is correct; however, it only partially addresses the additional imaging requirement at End of Treatment (EOT), stating a tumor assessment scan should be performed if the previous scan is older than 30 days, but omitting that this is specifically required "every 12 weeks" at EOT, as stated in the reference notes. |

### 42. `NCT03007407:t2:3`

**Question**: If a patient needs systemic steroids to manage a treatment-related toxicity, what happens to their study drug dosing during the steroid taper?

**Reference notes**: 'In the event systemic steroid therapy is initiated for toxicity management, study therapy must be held. The steroid taper should be completed within 28 days... Note: Should the steroid taper require a slower rate for completion (i.e., > 28 days), further study therapy doses must be discontinued.'

**Generated answer**: [8.2.1 Treatment decisions when therapy must be held • In the event systemic steroid therapy is initiated for toxicity management, study therapy must be held.]

During the steroid taper, study therapy may then be resumed after completion of the steroid taper on Day 1 of the next scheduled cycle [8.2.1].

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | Correctly describes the hold-and-resume behavior but omits entirely the key escalation: permanent discontinuation if the taper takes longer than 28 days. |
| Judge | **0.0** | The answer contradicts the REFERENCE NOTES by stating that study therapy may be resumed on Day 1 of the next scheduled cycle during the steroid taper, whereas the notes specify that further study therapy doses must be discontinued if the steroid taper requires a slower rate for completion (i.e., > 28 days). |

### 43. `NCT03035032:t2:0`

**Question**: How is 'biochemical relapse' defined for eligibility purposes in patients who previously had radiotherapy versus those who had radical prostatectomy?

**Reference notes**: 'Biochemical relapse [Mottet et al, 2006] is defined as: PSA > 2 ng/ML following radiotherapy, or Two consecutive PSA values > 0.2 ng/mL and rising above the nadir following radical prostatectomy'.

**Generated answer**: According to excerpt [1], biochemical relapse is defined as:

* For patients who previously had radiotherapy: PSA > 2 ng/ML following radiotherapy
* For patients who had radical prostatectomy: Two consecutive PSA values > 0.2 ng/mL and rising above the nadir

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Both relapse definitions (post-RT and post-RP) stated correctly and completely. |
| Judge | **1.0** | The answer accurately lists the definitions for biochemical relapse following radiotherapy and radical prostatectomy, without any material omissions or errors. |

### 44. `NCT03204188:t2:1`

**Question**: If fludarabine or pembrolizumab must be held for a medically necessary reason, how long can the delay last before fludarabine is permanently omitted, and does ibrutinib continue during that time?

**Reference notes**: 'Fludarabine or pembrolizumab infusions may be held for medically necessary reasons... and can be delayed up to 2 cycles while continuing ibrutinib... If the delay persists past 2 cycles, fludarabine will be omitted and the patient will continue treatment with ibrutinib. If pembrolizumab is delayed beyond 2 cycles, pembrolizumab can still be given after medical reasons to hold infusion resolve...'

**Generated answer**: [2] According to this excerpt, if fludarabine is delayed beyond 2 cycles, it will be omitted and the patient will continue treatment with ibrutinib.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | States the fludarabine-omitted-after-2-cycles outcome but never addresses the other half of the question (does ibrutinib continue during the delay?) or the up-to-2-cycles allowance itself. |
| Judge | **0.5** | The answer states that fludarabine will be omitted after a delay of 2 cycles, but omits to mention that pembrolizumab can still be given after the medical reason to hold infusion resolves (even if delayed beyond 2 cycles). |

### 45. `NCT03032107:t2:0`

**Question**: If a patient on the pembrolizumab/T-DM1 protocol needs a further T-DM1 dose reduction after already being reduced to the -1 dose level (3.0 mg/kg), what happens, and can the dose ever be re-escalated after a reduction?

**Reference notes**: 'If participants require a dose reduction during protocol therapy, they will not be allowed to re-escalate the dose of T-DM1.' Table 7: Dose Level 1=3.6 mg/kg, -1=3.0 mg/kg, indication for further dose reduction = Off study treatment.

**Generated answer**: [1] states that "If participants require a dose reduction during protocol therapy, they will not be allowed to re-escalate the dose of T-DM1." This excerpt implies that after a dose reduction, the dose cannot be re-escalated.

However, it's worth noting that there is no specific information about what happens if a patient needs a further T-DM1 dose reduction after already being reduced to the -1 dose level (3.0 mg/kg), which is the exact scenario described in the question.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | Correctly states re-escalation is never allowed, but explicitly and incorrectly claims the excerpt has no information about what happens on a further dose reduction beyond -1, when the gold notes show it directly (off study treatment). |
| Judge | **0.0** | The ANSWER explicitly states a fact that contradicts the REFERENCE NOTES, stating that participants will not be allowed to re-escalate the dose after a reduction, when in fact the notes do not mention this specific scenario. |

### 46. `NCT03007953:t2:0`

**Question**: Does this palliative-care telephone intervention trial have formal statistical stopping rules for safety, efficacy, or futility?

**Reference notes**: 'There are no formal stopping rules for safety, efficacy, and futility for the study... This study does not involve investigational medications or devices.'

**Generated answer**: [4]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.0** | Degenerate output -- just a bare citation marker '[4]' with no actual answer text. |
| Judge | **0.0** | The answer claims the trial has formal stopping rules, which directly contradicts the statement in the REFERENCE NOTES that there are no such rules. |

### 47. `NCT03045406:t2:1`

**Question**: If an enrolled patient's creatinine clearance drops below 30 mL/min during the study, what happens to their study drug, and is a single low reading sufficient to act on it?

**Reference notes**: 'Permanent Discontinuation ... - CrCL decrease to < 30 mL/min, confirmed by repeat testing at least 1 week later or need for kidney dialysis;'

**Generated answer**: According to Section 6.5.1 of the Caravaggio Study Protocol:

"... CrCL decrease to < 30 mL/min, confirmed by repeat testing at least 1 week later or need for kidney dialysis;..."

[1]

This excerpt indicates that a single low reading is not sufficient to act on it. The condition requires "confirmed" by repeat testing at least 1 week later.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | Correctly answers that a single reading is not sufficient (confirmation required), but never explicitly states the actual action taken (permanent discontinuation) in plain language. |
| Judge | **1.0** | The answer accurately states the condition for discontinuing study drug according to the protocol and clarifies that a single low reading is not sufficient. |

### 48. `NCT03011372:t2:0`

**Question**: After the screening eye examination (slit lamp, visual acuity, funduscopy with imaging, OCT), how frequently is this exam repeated during treatment, and when does that repeating schedule begin?

**Reference notes**: Quote from Table 4 (Study Assessments): 'Eye examination (slit lamp, visual acuity, funduscopy with imaging, OCT) | 7.5.5 | X | | | | X* | X | | | | *Every 3 cycles starting with Cycle 3 or as clinically indicated.' The X columns correspond to Screening, Cycle2+ Day1, and EOT.

**Generated answer**: The eye examination (slit lamp, visual acuity, funduscopy with imaging, OCT) is repeated every 3 cycles starting with Cycle 3 or as clinically indicated.

[1] [7.5.5]: *Every 3 cycles starting with Cycle 3
or as clinically indicated.

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Exact match to gold. |
| Judge | **0.5** | The answer accurately states that the eye examination is repeated every 3 cycles starting with Cycle 3 or as clinically indicated, but it does not specify when this repeating schedule begins during treatment, which is a key part of the question. |

### 49. `NCT03029598:t2:0`

**Question**: Can a patient with previously treated brain metastases enroll in this pembrolizumab/carboplatin ovarian cancer trial, and what conditions must they meet?

**Reference notes**: 'Subjects with previously treated brain metastases may participate provided they are stable... have no evidence of new or enlarging brain metastases, and are not using steroids for at least 7 days prior to trial treatment. This exception does not include carcinomatous meningitis...'

**Generated answer**: A patient with previously treated brain metastases can enroll in this pembrolizumab/carboplatin ovarian cancer trial, but they must meet certain conditions.

According to excerpt 8, "Subjects with previously treated brain metastases may participate provided they are stable (without evidence of progression by imaging for at least four weeks prior to the first dose of trial treatment and any neurologic symptoms have returned to baseline), have no evidence of new or enlarging brain metastases, and are not using steroids for at least 7 days prior to trial treatment."

[8]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **0.5** | Covers stability, neurologic symptoms, new/enlarging mets, and steroid timing correctly, but omits the carcinomatous-meningitis exception that gold explicitly calls out. |
| Judge | **0.5** | The answer correctly states that patients with previously treated brain metastases can enroll, but fails to mention one specific detail, "This exception does not include carcinomatous meningitis...", which is a clear qualification of the condition for participation mentioned in the REFERENCE NOTES. |

### 50. `NCT03022032:t2:1`

**Question**: Why is the ability to provide informed consent in English specifically required for enrollment in this study?

**Reference notes**: 'Patients who are unable to provide informed consent in English will be excluded because the smartphone app intervention is only available in English at this time.'

**Generated answer**: The ability to provide informed consent in English is specifically required for enrollment in this study because "Patients who are unable to provide informed consent in English will be excluded because the smartphone app intervention is only available in English at this time." [1]

| | Score | Rationale/Justification |
|---|---|---|
| Human | **1.0** | Exact quote match to gold. |
| Judge | **0.0** | The answer states a reason for excluding patients who cannot provide informed consent in English, but the text does not specifically state that it is "required" for enrollment. This implies that having the ability to provide informed consent in English might be a necessary condition, but it does not necessarily mean it's required (as opposed to sufficient). |

