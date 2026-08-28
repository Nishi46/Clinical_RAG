"""Shared text-normalization helpers -- factored out at S3-07 so T1's
exact_match_score and S4-03's outcome-phrasing comparison don't each grow
their own copy.

`extract_durations_in_months` exists specifically for the normalization
boundary `discrepancy_definition.md` names explicitly: "24 months" vs.
"2 years" is a match, not a divergence -- a difference attributable only to
unit/format. T1's timeframe questions (S3-03) hit this immediately, since a
generated answer restating a gold "24 months" as "2 years" is correct, not
wrong.
"""

from __future__ import annotations

import re

_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Casefold, strip punctuation, collapse whitespace."""
    stripped = _PUNCTUATION_PATTERN.sub("", text.lower())
    return _WHITESPACE_PATTERN.sub(" ", stripped).strip()


def contains_as_whole_token(needle: str, haystack: str) -> bool:
    """Word-boundary substring check: plain `in` would let a short numeric
    needle like "6" match inside "16" or "26". `\\b` treats digits and
    letters as the same "word" class, so "6" won't match inside "16" but
    will match a standalone "6" or "6" followed by punctuation/whitespace.
    Both arguments are expected to already be normalized (e.g. via
    `normalize_text`)."""
    if not needle:
        return False
    return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None


_DURATION_UNIT_TO_MONTHS = {
    "year": 12.0,
    "years": 12.0,
    "yr": 12.0,
    "yrs": 12.0,
    "month": 1.0,
    "months": 1.0,
    "mo": 1.0,
    "mos": 1.0,
    "week": 12.0 / 52.0,
    "weeks": 12.0 / 52.0,
    "wk": 12.0 / 52.0,
    "wks": 12.0 / 52.0,
    "day": 12.0 / 365.0,
    "days": 12.0 / 365.0,
}

_DURATION_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(years?|yrs?|months?|mos?|weeks?|wks?|days?)\b"
)


def extract_durations_in_months(text: str) -> set[float]:
    """Every "<number> <duration-unit>" occurrence in `text`, each
    converted to months and rounded to 2 decimals (to absorb the
    week/day-to-month floating-point approximation). Lets a short gold
    value like "24 months" be compared against a longer generated sentence
    that restates the same duration in a different unit, rather than
    requiring a literal string match."""
    durations = set()
    for match in _DURATION_PATTERN.finditer(text.lower()):
        value = float(match.group(1))
        unit = match.group(2)
        durations.add(round(value * _DURATION_UNIT_TO_MONTHS[unit], 2))
    return durations
