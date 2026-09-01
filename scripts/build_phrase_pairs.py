#!/usr/bin/env python3
"""Builds S4-03's hand-labeled phrase-pair validation set (spec item 5) --
`data/normalization/phrase_pairs.jsonl`. Every pair and its label is
deliberately authored, not sampled or scraped: the 24 timeframe-rewording
pairs are a template matrix (construct x unit-equivalent duration pair)
because that category is mechanically well-defined -- any construct held
fixed across a unit-equivalent duration rewording is a match, by
`discrepancy_definition.md` SS3's own example ("24 months" vs. "2 years")
-- everything else (phrasing matches, construct swaps, comparator-arm
changes, population-threshold changes, primary/secondary flips, and the
three ambiguous categories) is individually hand-written below, including
the confirmed real `NCT02872116` divergence example from
`discrepancy_definition.md` SS3, used verbatim.

Committed (not a scratch throwaway) so the set's construction is
reproducible and auditable, same convention as `scripts/run_ablation.py`.
Re-running this script regenerates the file from scratch -- it is not
meant to be hand-edited afterward.
"""

from __future__ import annotations

import json
from pathlib import Path

from protocol_drift.normalize.outcome import PhrasePair

OUTPUT_PATH = Path("data/normalization/phrase_pairs.jsonl")

# --- MATCH: timeframe/unit rewording (mechanical matrix) --------------------

_CONSTRUCTS = [
    "Overall survival (OS)",
    "Progression-free survival (PFS)",
    "Objective response rate (ORR)",
    "Duration of response (DOR)",
    "Disease-free survival (DFS)",
    "Time to progression (TTP)",
    "Overall response rate",
    "Event-free survival (EFS)",
]

_DURATION_PAIRS = [
    ("24 months", "2 years"),
    ("12 months", "1 year"),
    ("52 weeks", "1 year"),
]


def _timeframe_reword_pairs() -> list[PhrasePair]:
    pairs = []
    i = 0
    for construct in _CONSTRUCTS:
        for dur_a, dur_b in _DURATION_PAIRS:
            i += 1
            pairs.append(
                PhrasePair(
                    pair_id=f"timeframe_reword:{i:02d}",
                    outcome_a=f"{construct} at {dur_a}",
                    outcome_b=f"{construct} at {dur_b}",
                    label="match",
                    category="timeframe_reword",
                    note="Same construct, unit-equivalent duration rewording -- "
                    'discrepancy_definition.md SS3\'s own example ("24 months" vs. "2 years").',
                )
            )
    return pairs


# --- MATCH: minor phrasing / abbreviation ------------------------------------

_PHRASING_MATCHES = [
    ("Overall survival", "Overall Survival (OS)"),
    ("Progression free survival", "PFS"),
    (
        "Objective response rate (ORR), evaluated per RECIST 1.1",
        "Objective Response Rate (ORR) as assessed by RECIST version 1.1 criteria",
    ),
    (
        "Time from randomization to death from any cause",
        "Overall survival, defined as time from randomization to death due to any cause",
    ),
    (
        "Change in HbA1c from baseline",
        "Change from Baseline in Hemoglobin A1c (HbA1c)",
    ),
    ("Duration of Response (DoR)", "DOR"),
    (
        "Percentage of Participants Achieving Complete Response (CR)",
        "Complete response rate",
    ),
    (
        "Pain intensity as measured by the Brief Pain Inventory (BPI)",
        "BPI-measured pain intensity score",
    ),
    ("Time to first subsequent therapy", "TFST"),
    (
        "Progression-Free Survival (PFS) per RECIST 1.1, assessed by investigator",
        "Investigator-assessed PFS per RECIST v1.1",
    ),
    (
        "Health-related quality of life as measured by EORTC QLQ-C30",
        "EORTC QLQ-C30 quality of life score",
    ),
    ("Overall Survival (OS)", "OS"),
    ("Rate of pathologic complete response (pCR)", "Pathologic Complete Response Rate"),
    (
        "Percentage of participants with Grade 3 or higher adverse events",
        "Incidence of Grade >=3 AEs",
    ),
    ("Median time to progression or death", "Progression-Free Survival (PFS)"),
    ("Change from baseline in tumor size by RECIST 1.1", "RECIST 1.1 tumor size change"),
]


def _phrasing_match_pairs() -> list[PhrasePair]:
    return [
        PhrasePair(
            pair_id=f"phrasing:{i:02d}",
            outcome_a=a,
            outcome_b=b,
            label="match",
            category="phrasing",
            note="Same construct, abbreviation/rewording only.",
        )
        for i, (a, b) in enumerate(_PHRASING_MATCHES, start=1)
    ]


# --- DIVERGENCE: the confirmed real example ----------------------------------


def _checkmate649_pair() -> PhrasePair:
    # Verbatim from discrepancy_definition.md SS3.
    return PhrasePair(
        pair_id="checkmate649",
        outcome_a=(
            "Overall survival (OS) of nivolumab + ipilimumab versus oxaliplatin + "
            "fluoropyrimidine in subjects with PD-L1 expressing tumors"
        ),
        outcome_b=(
            "Overall Survival (OS) in Participants Treated With Nivolumab Plus Chemotherapy "
            "vs Chemotherapy With PD-L1 CPS >= 5"
        ),
        label="divergence",
        category="checkmate649",
        note=(
            "Confirmed real example (NCT02872116, S0-01): comparator arm changed "
            "(ipilimumab-combination -> chemotherapy-combination) and population/threshold "
            "changed (any PD-L1 expression -> CPS >= 5)."
        ),
    )


# --- DIVERGENCE: different clinical construct --------------------------------

_CONSTRUCT_SWAPS = [
    ("Overall survival (OS)", "Progression-free survival (PFS)"),
    ("Objective response rate (ORR)", "Duration of response (DOR)"),
    ("Disease-free survival (DFS)", "Overall survival (OS)"),
    ("Event-free survival (EFS)", "Progression-free survival (PFS)"),
    ("Time to progression (TTP)", "Overall survival (OS)"),
    ("Complete response rate", "Objective response rate"),
    ("Pathologic complete response (pCR) rate", "Event-free survival (EFS)"),
    ("Change from baseline in tumor size", "Overall survival (OS)"),
]


def _construct_swap_pairs() -> list[PhrasePair]:
    return [
        PhrasePair(
            pair_id=f"construct_swap:{i:02d}",
            outcome_a=a,
            outcome_b=b,
            label="divergence",
            category="construct_swap",
            note="Different clinical construct entirely.",
        )
        for i, (a, b) in enumerate(_CONSTRUCT_SWAPS, start=1)
    ]


# --- DIVERGENCE: comparator arm changed ---------------------------------------

_COMPARATOR_ARM_CHANGES = [
    (
        "Efficacy of drug X plus chemotherapy versus chemotherapy alone",
        "Efficacy of drug X monotherapy versus chemotherapy alone",
    ),
    (
        "Nivolumab plus ipilimumab versus chemotherapy",
        "Nivolumab monotherapy versus chemotherapy",
    ),
    ("Drug A versus placebo", "Drug A plus Drug B versus placebo"),
    (
        "Combination of pembrolizumab and axitinib versus sunitinib",
        "Pembrolizumab monotherapy versus sunitinib",
    ),
    ("Treatment with Drug X versus Drug Y", "Treatment with Drug X versus placebo"),
    (
        "Combination therapy versus best supportive care",
        "Combination therapy versus active comparator",
    ),
    (
        "Study drug versus standard chemotherapy regimen A",
        "Study drug versus standard chemotherapy regimen B",
    ),
    (
        "Randomized to Arm A (high-dose) or Arm B (low-dose)",
        "Randomized to Arm A (high-dose) or Arm C (placebo)",
    ),
    (
        "Response rate with Drug X added to standard of care",
        "Response rate with standard of care alone",
    ),
]


def _comparator_arm_pairs() -> list[PhrasePair]:
    return [
        PhrasePair(
            pair_id=f"comparator_arm:{i:02d}",
            outcome_a=a,
            outcome_b=b,
            label="divergence",
            category="comparator_arm",
            note="Comparator/treatment arm changed.",
        )
        for i, (a, b) in enumerate(_COMPARATOR_ARM_CHANGES, start=1)
    ]


# --- DIVERGENCE: population/threshold changed --------------------------------

_POPULATION_THRESHOLD_CHANGES = [
    (
        "Overall survival in patients with PD-L1 expression >=1%",
        "Overall survival in patients with PD-L1 expression >=50%",
    ),
    (
        "Progression-free survival in patients aged 18 and older",
        "Progression-free survival in patients aged 65 and older",
    ),
    (
        "Response rate in patients with any tumor stage",
        "Response rate in patients with Stage III-IV disease only",
    ),
    (
        "Overall survival in the intent-to-treat population",
        "Overall survival in patients with measurable disease at baseline",
    ),
    (
        "Efficacy in patients with EGFR-mutation positive tumors",
        "Efficacy in patients with EGFR exon 19 deletion tumors",
    ),
    (
        "Safety in patients with mild to moderate renal impairment",
        "Safety in patients with severe renal impairment",
    ),
    (
        "Response rate in HER2-positive breast cancer patients",
        "Response rate in HER2-low breast cancer patients",
    ),
    (
        "PFS in patients with BRCA1/2 mutations",
        "PFS in patients regardless of BRCA mutation status",
    ),
    (
        "Overall survival in treatment-naive patients",
        "Overall survival in previously treated patients",
    ),
]


def _population_threshold_pairs() -> list[PhrasePair]:
    return [
        PhrasePair(
            pair_id=f"population_threshold:{i:02d}",
            outcome_a=a,
            outcome_b=b,
            label="divergence",
            category="population_threshold",
            note="Materially different population/threshold definition.",
        )
        for i, (a, b) in enumerate(_POPULATION_THRESHOLD_CHANGES, start=1)
    ]


# --- DIVERGENCE: primary/secondary designation flipped -----------------------

_PRIMARY_SECONDARY_FLIPS = [
    ("Overall survival (OS) - Primary Endpoint", "Overall survival (OS) - Secondary Endpoint"),
    ("Progression-free survival, primary measure", "Progression-free survival, secondary measure"),
    (
        "Objective response rate (co-primary endpoint)",
        "Objective response rate (exploratory endpoint)",
    ),
    ("Duration of response - key secondary outcome", "Duration of response - primary outcome"),
    (
        "Overall survival, the study's primary endpoint",
        "Overall survival, an exploratory endpoint",
    ),
    (
        "Pathologic complete response rate (primary endpoint)",
        "Pathologic complete response rate (secondary endpoint)",
    ),
    ("Time to progression - primary endpoint", "Time to progression - secondary endpoint"),
    ("Quality of life score - secondary endpoint", "Quality of life score - primary endpoint"),
]


def _primary_secondary_pairs() -> list[PhrasePair]:
    return [
        PhrasePair(
            pair_id=f"primary_secondary:{i:02d}",
            outcome_a=a,
            outcome_b=b,
            label="divergence",
            category="primary_secondary",
            note="Same measure, different primary-vs-secondary designation.",
        )
        for i, (a, b) in enumerate(_PRIMARY_SECONDARY_FLIPS, start=1)
    ]


# --- AMBIGUOUS: vague/substantial rewording -----------------------------------

_VAGUE_REWORDINGS = [
    ("Clinical benefit as assessed by the investigator", "Overall treatment response"),
    ("Improvement in patient-reported symptoms", "Change in symptom burden score"),
    ("Efficacy of the study regimen", "Antitumor activity of the study regimen"),
    ("Safety and tolerability of the study drug", "Incidence of dose-limiting toxicities"),
    ("Time to clinical improvement", "Time to recovery"),
    ("Disease control rate", "Clinical benefit rate"),
    (
        "Composite endpoint of major adverse cardiovascular events",
        "Cardiovascular safety outcomes",
    ),
    ("Biomarker response", "Pharmacodynamic response"),
    ("Functional status improvement", "Change in performance status"),
    ("Overall treatment success", "Treatment response rate"),
]


def _vague_rewording_pairs() -> list[PhrasePair]:
    return [
        PhrasePair(
            pair_id=f"vague_rewording:{i:02d}",
            outcome_a=a,
            outcome_b=b,
            label="ambiguous",
            category="vague_rewording",
            note="Wording changed enough that confident classification isn't possible either way.",
        )
        for i, (a, b) in enumerate(_VAGUE_REWORDINGS, start=1)
    ]


# --- AMBIGUOUS: partial overlap (multiple outcomes vs. one) ------------------

_PARTIAL_OVERLAPS = [
    ("Overall survival and progression-free survival", "Overall survival"),
    ("Objective response rate; duration of response", "Duration of response"),
    (
        "Overall survival, progression-free survival, and objective response rate",
        "Progression-free survival",
    ),
    ("Safety and efficacy (overall survival)", "Overall survival"),
    ("Complete response rate and partial response rate", "Overall response rate"),
    ("Change in tumor size and change in biomarker levels", "Change in tumor size"),
    ("Time to progression and time to treatment failure", "Time to treatment failure"),
]


def _partial_overlap_pairs() -> list[PhrasePair]:
    return [
        PhrasePair(
            pair_id=f"partial_overlap:{i:02d}",
            outcome_a=a,
            outcome_b=b,
            label="ambiguous",
            category="partial_overlap",
            note="Multiple primary outcomes present in one source and not the other.",
        )
        for i, (a, b) in enumerate(_PARTIAL_OVERLAPS, start=1)
    ]


# --- AMBIGUOUS: population scope ----------------------------------------------

_POPULATION_SCOPE_AMBIGUITIES = [
    (
        "Efficacy in the overall study population",
        "Efficacy in a subset of patients meeting protocol-defined criteria",
    ),
    ("Response rate in evaluable patients", "Response rate in all treated patients"),
    (
        "Safety in patients receiving at least one dose",
        "Safety in patients completing the full treatment course",
    ),
    (
        "Progression-free survival in the per-protocol population",
        "Progression-free survival in the modified intent-to-treat population",
    ),
    ("Overall survival, censored at data cutoff", "Overall survival, with long-term follow-up"),
    (
        "Response in patients with prior systemic therapy",
        "Response in patients with prior therapy of any kind",
    ),
    (
        "Efficacy in patients with adequate organ function",
        "Efficacy in all randomized patients",
    ),
    (
        "Outcome assessed in the safety population",
        "Outcome assessed in the efficacy-evaluable population",
    ),
]


def _population_scope_pairs() -> list[PhrasePair]:
    return [
        PhrasePair(
            pair_id=f"population_scope:{i:02d}",
            outcome_a=a,
            outcome_b=b,
            label="ambiguous",
            category="population_scope",
            note="Population scope wording changed without a clearly material threshold change.",
        )
        for i, (a, b) in enumerate(_POPULATION_SCOPE_AMBIGUITIES, start=1)
    ]


def build_phrase_pairs() -> list[PhrasePair]:
    return [
        *_timeframe_reword_pairs(),
        *_phrasing_match_pairs(),
        _checkmate649_pair(),
        *_construct_swap_pairs(),
        *_comparator_arm_pairs(),
        *_population_threshold_pairs(),
        *_primary_secondary_pairs(),
        *_vague_rewording_pairs(),
        *_partial_overlap_pairs(),
        *_population_scope_pairs(),
    ]


def main() -> None:
    pairs = build_phrase_pairs()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        for pair in pairs:
            f.write(
                json.dumps(
                    {
                        "pair_id": pair.pair_id,
                        "outcome_a": pair.outcome_a,
                        "outcome_b": pair.outcome_b,
                        "label": pair.label,
                        "category": pair.category,
                        "note": pair.note,
                    }
                )
                + "\n"
            )

    from collections import Counter

    by_label = Counter(p.label for p in pairs)
    print(f"Wrote {len(pairs)} pair(s) -> {OUTPUT_PATH}")
    print(f"  by label: {dict(by_label)}")


if __name__ == "__main__":
    main()
