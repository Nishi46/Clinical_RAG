"""Discrepancy scorer -- S4-08.

Precision/recall/F1 for the discrepancy detector (S4-05), scored **per pair
type** (first-posted-vs-current, current-vs-protocol, registry-vs-results)
and pooled, per `discrepancy_definition.md` SS2's table -- the three pairs
are genuinely distinct signals, not one task, so collapsing them into a
single score would hide which comparison the detector is actually good at.

`divergence` is the positive class (SS3). A case where either the human
label or the detector's verdict is `ambiguous` is **excluded** from the
binary confusion matrix and tallied into its own bucket instead -- per the
definition doc's framing, `ambiguous` is neither a clean true-positive nor
a false-positive without human review, and silently coercing it into a
binary would misrepresent both precision and recall. A predicted verdict
with `retrieval_failed=True` (or no verdict at all) is handled the same
way, in its own separate bucket -- SS4-05 step 3 explicitly distinguishes
"we looked and couldn't decide" (ambiguous) from "we don't know" (retrieval
failure), and retrieval failure must never default to counting as
divergence.

This module defines its own `GoldLabel`/`PredictedVerdict` shapes rather
than importing them from S4-05/S4-07 -- as of this writing neither
`discrepancy/detector.py` (S4-05) nor `data/eval/t3_gold_labels.jsonl`
(S4-07) exist yet in this repo, so `score_discrepancy_detection` is written
against the plain (nct_id, pair, verdict) contract those modules will need
to produce, and is fully testable today against hand-constructed data (see
S4-08 step 6) with zero dependency on the real pipeline. `load_gold_labels`
and `load_detector_reports` below give the eventual file-loading glue, but
running `main()` end-to-end -- and therefore writing a real
`docs/discrepancy_eval.md` -- still waits on S4-01 through S4-07 landing.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

PairType = Literal["first_posted_vs_current", "current_vs_protocol", "registry_vs_results"]
Verdict = Literal["match", "divergence", "ambiguous"]

PAIR_TYPES: tuple[PairType, ...] = (
    "first_posted_vs_current",
    "current_vs_protocol",
    "registry_vs_results",
)

_DEFAULT_Z = 1.96  # 95% confidence


@dataclass(frozen=True)
class GoldLabel:
    """One blind-adjudicated verdict (S4-07) for one trial/pair."""

    nct_id: str
    pair: PairType
    verdict: Verdict
    justification: str = ""


@dataclass(frozen=True)
class PredictedVerdict:
    """One detector verdict (S4-05) for one trial/pair. `verdict` is `None`
    when `retrieval_failed` is True -- a verdict-less prediction with
    `retrieval_failed=False` is treated as retrieval failure too, since
    "no verdict, no flag" is a malformed producer, not a valid third
    state."""

    nct_id: str
    pair: PairType
    verdict: Verdict | None
    retrieval_failed: bool = False


def wilson_interval(successes: int, n: int, z: float = _DEFAULT_Z) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion -- chosen over a
    normal (Wald) interval because it stays inside [0, 1] and doesn't
    degenerate at p=0/1, both of which matter at this sample's small n
    (40-60 per `sprint_plan.md`). Closed-form, no `scipy` dependency:
    `scipy.stats` isn't in this project's `eval` extra (only `rapidfuzz`
    is), and the formula is a handful of lines, same call `calibration.py`
    made for weighted kappa."""
    if n <= 0:
        raise ValueError("wilson_interval requires n > 0")
    phat = successes / n
    denom = 1 + z**2 / n
    center = phat + z**2 / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2))
    lower = (center - margin) / denom
    upper = (center + margin) / denom
    return max(0.0, lower), min(1.0, upper)


def _rate_and_ci(successes: int, n: int) -> tuple[float | None, tuple[float, float] | None]:
    if n == 0:
        return None, None
    return successes / n, wilson_interval(successes, n)


@dataclass
class PairScore:
    pair: PairType | Literal["pooled"]
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    retrieval_failed: int = 0
    not_applicable: int = 0
    ambiguous_bucket: dict[str, int] = field(default_factory=dict)

    @property
    def n_scored(self) -> int:
        """Count entering the binary confusion matrix -- excludes
        ambiguous, retrieval-failed, and not-applicable cases."""
        return self.tp + self.fp + self.fn + self.tn

    @property
    def precision(self) -> float | None:
        return _rate_and_ci(self.tp, self.tp + self.fp)[0]

    @property
    def precision_ci(self) -> tuple[float, float] | None:
        return _rate_and_ci(self.tp, self.tp + self.fp)[1]

    @property
    def recall(self) -> float | None:
        return _rate_and_ci(self.tp, self.tp + self.fn)[0]

    @property
    def recall_ci(self) -> tuple[float, float] | None:
        return _rate_and_ci(self.tp, self.tp + self.fn)[1]

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or precision + recall == 0:
            return None
        return 2 * precision * recall / (precision + recall)


@dataclass
class DiscrepancyScores:
    per_pair: dict[PairType, PairScore]
    pooled: PairScore


def _score_one(gold: GoldLabel, pred: PredictedVerdict | None, into: PairScore) -> None:
    if pred is None:
        into.not_applicable += 1
        return

    verdict = pred.verdict
    if pred.retrieval_failed or verdict is None:
        into.retrieval_failed += 1
        return

    if gold.verdict == "ambiguous" or verdict == "ambiguous":
        key = f"gold={gold.verdict},pred={verdict}"
        into.ambiguous_bucket[key] = into.ambiguous_bucket.get(key, 0) + 1
        return

    gold_positive = gold.verdict == "divergence"
    pred_positive = verdict == "divergence"
    if gold_positive and pred_positive:
        into.tp += 1
    elif pred_positive:
        into.fp += 1
    elif gold_positive:
        into.fn += 1
    else:
        into.tn += 1


def score_discrepancy_detection(
    gold_labels: Sequence[GoldLabel],
    detector_reports: Sequence[PredictedVerdict],
) -> DiscrepancyScores:
    """Matches each gold label to a predicted verdict by (nct_id, pair) and
    scores per pair type and pooled. A gold label with no matching
    prediction (e.g. `registry_vs_results` when the trial has no reported
    results -- expected, per `discrepancy_definition.md` SS2, not an error)
    is counted in `not_applicable`, not silently dropped."""
    predictions_by_key = {(p.nct_id, p.pair): p for p in detector_reports}

    per_pair: dict[PairType, PairScore] = {pair: PairScore(pair=pair) for pair in PAIR_TYPES}
    pooled = PairScore(pair="pooled")

    for gold in gold_labels:
        pred = predictions_by_key.get((gold.nct_id, gold.pair))
        _score_one(gold, pred, per_pair[gold.pair])
        _score_one(gold, pred, pooled)

    return DiscrepancyScores(per_pair=per_pair, pooled=pooled)


_PAIR_LABELS: dict[PairType, str] = {
    "first_posted_vs_current": "First-posted vs. current registry",
    "current_vs_protocol": "Current registry vs. protocol",
    "registry_vs_results": "Registry vs. results-reported",
}


def _fmt(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


def _fmt_ci(ci: tuple[float, float] | None) -> str:
    return f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci is not None else "n/a"


def render_discrepancy_eval_md(scores: DiscrepancyScores, n_gold_trials: int) -> str:
    """Precision is reported first and most prominently, per
    `project_plan.md` SS7.2: "a false discrepancy accusation is far more
    costly than a miss.\""""
    lines = ["# Discrepancy detection eval", ""]
    lines.append(
        f"Scored against {n_gold_trials} blind-adjudicated trials (S4-07). Positive class: "
        "`divergence`. `ambiguous` (human or detector) and detector `retrieval_failed` cases are "
        "excluded from precision/recall/F1 and reported separately below -- see "
        "`discrepancy_definition.md` SS3/SS4-05."
    )
    lines.append("")
    lines.append("## Precision / recall / F1, per pair type and pooled")
    lines.append("")
    lines.append("| Pair | n scored | Precision | 95% CI | Recall | 95% CI | F1 |")
    lines.append("|---|---|---|---|---|---|---|")
    for pair in PAIR_TYPES:
        s = scores.per_pair[pair]
        lines.append(
            f"| {_PAIR_LABELS[pair]} | {s.n_scored} | {_fmt(s.precision)} | "
            f"{_fmt_ci(s.precision_ci)} | {_fmt(s.recall)} | {_fmt_ci(s.recall_ci)} | "
            f"{_fmt(s.f1)} |"
        )
    p = scores.pooled
    lines.append(
        f"| **Pooled** | {p.n_scored} | {_fmt(p.precision)} | {_fmt_ci(p.precision_ci)} | "
        f"{_fmt(p.recall)} | {_fmt_ci(p.recall_ci)} | {_fmt(p.f1)} |"
    )
    lines.append("")

    lines.append("## Ambiguous bucket (excluded from the table above)")
    lines.append("")
    lines.append("| Pair | gold/pred combination | n |")
    lines.append("|---|---|---|")
    labeled_scores: list[tuple[str, PairScore]] = [
        (_PAIR_LABELS[pair], scores.per_pair[pair]) for pair in PAIR_TYPES
    ]
    labeled_scores.append(("Pooled", scores.pooled))
    for label, s in labeled_scores:
        for combo, count in sorted(s.ambiguous_bucket.items()):
            lines.append(f"| {label} | {combo} | {count} |")
    lines.append("")

    lines.append("## Retrieval failure / not applicable, per pair type")
    lines.append("")
    lines.append("| Pair | retrieval_failed | not_applicable |")
    lines.append("|---|---|---|")
    for pair in PAIR_TYPES:
        s = scores.per_pair[pair]
        lines.append(f"| {_PAIR_LABELS[pair]} | {s.retrieval_failed} | {s.not_applicable} |")
    lines.append("")

    return "\n".join(lines)


def load_gold_labels(path: Path) -> list[GoldLabel]:
    """Loads `data/eval/t3_gold_labels.jsonl` (S4-07): one JSON object per
    line with `nct_id`, `pair`, `verdict`, and an optional
    `justification`."""
    labels = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels.append(
            GoldLabel(
                nct_id=row["nct_id"],
                pair=row["pair"],
                verdict=row["verdict"],
                justification=row.get("justification", ""),
            )
        )
    return labels


def load_detector_reports(reports_dir: Path) -> list[PredictedVerdict]:
    """Loads `data/discrepancy/reports/{nct_id}.json` (S4-05): one file per
    trial, each a JSON object with `nct_id` and a `pairs` map keyed by pair
    type to `{"verdict": ..., "retrieval_failed": ...}` (or `null` for a
    pair that doesn't apply to this trial, e.g. no results reported)."""
    predictions = []
    for report_path in sorted(reports_dir.glob("*.json")):
        row = json.loads(report_path.read_text())
        nct_id = row["nct_id"]
        for pair, pair_row in row["pairs"].items():
            if pair_row is None:
                continue
            predictions.append(
                PredictedVerdict(
                    nct_id=nct_id,
                    pair=pair,
                    verdict=pair_row.get("verdict"),
                    retrieval_failed=pair_row.get("retrieval_failed", False),
                )
            )
    return predictions


DEFAULT_GOLD_PATH = Path("data/eval/t3_gold_labels.jsonl")
DEFAULT_REPORTS_DIR = Path("data/discrepancy/reports")
DEFAULT_OUTPUT_PATH = Path("docs/discrepancy_eval.md")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score the discrepancy detector against S4-07's gold labels."
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--reports", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    if not args.gold.exists():
        raise SystemExit(
            f"{args.gold} does not exist -- S4-07's hand-adjudication hasn't produced gold labels "
            "yet, so there is nothing to score."
        )
    if not args.reports.exists():
        raise SystemExit(
            f"{args.reports} does not exist -- S4-05's detector hasn't produced reports yet, so "
            "there is nothing to score."
        )

    gold_labels = load_gold_labels(args.gold)
    detector_reports = load_detector_reports(args.reports)
    n_gold_trials = len({label.nct_id for label in gold_labels})

    scores = score_discrepancy_detection(gold_labels, detector_reports)
    rendered = render_discrepancy_eval_md(scores, n_gold_trials)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
