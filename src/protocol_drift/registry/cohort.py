"""Cohort query and freeze — oncology trials with a posted protocol/SAP.

Query params and field names below are confirmed live against /api/v2/studies
(not assumed from docs): ``query.cond=cancer`` + ``filter.overallStatus=COMPLETED``
+ ``filter.advanced=AREA[StudyFirstPostDate]RANGE[2017-01-01,MAX]`` +
``aggFilters=results:with`` returns the same 3,132-candidate pool S0-04 found.
``hasProtocol``/``hasSap`` are per-document fields on
``documentSection.largeDocumentModule.largeDocs[]`` and cannot be filtered
server-side (scratch/field_paths.md S0-01 finding) — filtered client-side here.

Oncology was chosen in scratch/therapeutic_area_decision.md (S0-04): pool size
and doc-having rate are effectively saturated for either candidate area, but
oncology showed materially denser shared drug-name vocabulary, which is the
scope doc's explicit selection criterion.

Selection is a pure function of a frozen candidate list (`select_cohort`), so
determinism can be verified by re-running it against the same cached input
without depending on the live registry staying byte-identical across two
network calls.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from protocol_drift.registry.client import RegistryClient

CONDITION_QUERY = "cancer"
CANDIDATE_FIELDS = (
    "NCTId,LeadSponsorName,LeadSponsorClass,Phase,"
    "LargeDocHasProtocol,LargeDocHasSAP,StudyFirstPostDate,OverallStatus"
)
DEFAULT_TARGET = 200
DEFAULT_MAX_PER_SPONSOR = 3

DEFAULT_CANDIDATES_CACHE = Path("data/cohort_candidates_raw.json")
DEFAULT_COHORT_OUT = Path("data/cohort.json")


def candidate_query() -> dict[str, Any]:
    """Confirmed-working query params for the oncology candidate pool."""
    return {
        "query.cond": CONDITION_QUERY,
        "filter.overallStatus": "COMPLETED",
        "filter.advanced": "AREA[StudyFirstPostDate]RANGE[2017-01-01,MAX]",
        "aggFilters": "results:with",
        "fields": CANDIDATE_FIELDS,
    }


def has_required_doc(study: dict[str, Any]) -> bool:
    """True if the study has at least one protocol or SAP document attached.

    Per field_paths.md, hasProtocol/hasSap live per-document, not as a
    top-level searchable field — this is the client-side check that
    replaces the (nonexistent) server-side filter.
    """
    docs = study.get("documentSection", {}).get("largeDocumentModule", {}).get("largeDocs", [])
    return any(d.get("hasProtocol") or d.get("hasSap") for d in docs)


def extract_candidate(study: dict[str, Any]) -> dict[str, Any]:
    """Pull the slim stratification fields out of a candidate study record."""
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    sponsor = protocol.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    phases = protocol.get("designModule", {}).get("phases", [])
    return {
        "nct_id": ident["nctId"],
        "sponsor_name": sponsor.get("name", "UNKNOWN"),
        "sponsor_class": sponsor.get("class", "UNKNOWN"),
        "phase": "|".join(phases) if phases else "NA",
        "first_posted": status.get("studyFirstPostDateStruct", {}).get("date"),
    }


def fetch_candidates(client: RegistryClient) -> list[dict[str, Any]]:
    """Sweep the candidate pool, keep only doc-having trials, return slim rows."""
    candidates = []
    for study in client.search_studies(**candidate_query()):
        if not has_required_doc(study):
            continue
        candidates.append(extract_candidate(study))
    return candidates


def select_cohort(
    candidates: list[dict[str, Any]],
    target: int = DEFAULT_TARGET,
    max_per_sponsor: int = DEFAULT_MAX_PER_SPONSOR,
) -> list[dict[str, Any]]:
    """Deterministically select up to `target` trials, stratified by
    (sponsor_class, phase) via a largest-remainder proportional quota, capped
    at `max_per_sponsor` trials per sponsor.

    Pure function of `candidates` — same input always produces the same
    output, since every ordering decision sorts by nct_id or by stratum key
    rather than relying on dict/set iteration order or wall-clock randomness.
    """
    dedup: dict[str, dict[str, Any]] = {}
    for c in candidates:
        dedup.setdefault(c["nct_id"], c)
    pool = sorted(dedup.values(), key=lambda c: c["nct_id"])

    if not pool:
        raise ValueError("no candidates to select from")
    target = min(target, len(pool))

    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in pool:
        key = (c["sponsor_class"], c["phase"])
        strata.setdefault(key, []).append(c)

    stratum_keys = sorted(strata)
    total = len(pool)
    raw_quota = {k: len(strata[k]) / total * target for k in stratum_keys}
    quota = {k: int(raw_quota[k]) for k in stratum_keys}
    shortfall = target - sum(quota.values())
    # Largest-remainder method: give the leftover slots to the strata with the
    # biggest fractional part, tie-broken by stratum key for determinism.
    by_remainder = sorted(stratum_keys, key=lambda k: (-(raw_quota[k] - quota[k]), k))
    for k in by_remainder[:shortfall]:
        quota[k] += 1

    selected: list[dict[str, Any]] = []
    sponsor_counts: dict[str, int] = {}
    leftover: list[dict[str, Any]] = []

    for k in stratum_keys:
        taken = 0
        for c in strata[k]:
            eligible = (
                taken < quota[k] and sponsor_counts.get(c["sponsor_name"], 0) < max_per_sponsor
            )
            if eligible:
                selected.append(c)
                sponsor_counts[c["sponsor_name"]] = sponsor_counts.get(c["sponsor_name"], 0) + 1
                taken += 1
            else:
                leftover.append(c)

    if len(selected) < target:
        for c in sorted(leftover, key=lambda c: c["nct_id"]):
            if len(selected) >= target:
                break
            if sponsor_counts.get(c["sponsor_name"], 0) >= max_per_sponsor:
                continue
            selected.append(c)
            sponsor_counts[c["sponsor_name"]] = sponsor_counts.get(c["sponsor_name"], 0) + 1

    return sorted(selected, key=lambda c: c["nct_id"])


def stratification_summary(selected: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in selected:
        key = f"{c['sponsor_class']}|{c['phase']}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def write_candidates_cache(candidates: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(candidates, key=lambda c: c["nct_id"])
    path.write_text(json.dumps(ordered, indent=2, sort_keys=True) + "\n")


def load_candidates_cache(path: Path) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(path.read_text())
    return data


def write_cohort_manifest(
    selected: list[dict[str, Any]],
    summary: dict[str, int],
    path: Path,
) -> None:
    payload = {
        "condition": "oncology",
        "filters": candidate_query(),
        "count": len(selected),
        "stratification_summary": summary,
        "trials": selected,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze the Sprint 1 oncology cohort.")
    parser.add_argument("--candidates-cache", type=Path, default=DEFAULT_CANDIDATES_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_COHORT_OUT)
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET)
    parser.add_argument(
        "--use-cached",
        action="store_true",
        help="reuse the cached candidate list instead of hitting the live API "
        "(use this to verify determinism without depending on the registry "
        "staying identical across two live calls)",
    )
    args = parser.parse_args()

    if args.use_cached:
        candidates = load_candidates_cache(args.candidates_cache)
    else:
        client = RegistryClient()
        candidates = fetch_candidates(client)
        write_candidates_cache(candidates, args.candidates_cache)

    selected = select_cohort(candidates, target=args.target)
    summary = stratification_summary(selected)
    write_cohort_manifest(selected, summary, args.out)
    print(f"Selected {len(selected)} trials -> {args.out}")
    print(f"Stratification: {summary}")


if __name__ == "__main__":
    main()
