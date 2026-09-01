"""Query metadata prefilter parsing -- S3-10.

Most T1/T2 questions don't carry an explicit NCT ID inline -- they're asked
in a single-trial context the eval harness already knows (it always has
`question.nct_id`), so `parse_query_filters` accepts that as an optional
override in addition to parsing the query text itself. The caller-supplied
`nct_id` wins when both are present: a regex hit inside free text could be
a false positive (a different trial's ID mentioned in passing, e.g. "unlike
NCT01234567, this trial requires..."), while the caller's own context is
authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_NCT_ID_PATTERN = re.compile(r"NCT\d{8}", re.IGNORECASE)
_VERSION_PATTERN = re.compile(r"(?:amendment|version)\s+(\d+(?:\.\d+)?)", re.IGNORECASE)

# Checked longest-phrase-first so "statistical analysis plan" doesn't fall
# through to a coincidental "plan" match, and "sap" is checked before the
# generic "protocol" fallback.
_DOC_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("statistical analysis plan", "sap"),
    ("sap", "sap"),
    ("protocol", "protocol"),
)


@dataclass(frozen=True)
class QueryFilters:
    nct_id: str | None = None
    doc_type: str | None = None
    doc_version: float | None = None
    section: str | None = None


def parse_query_filters(query_text: str, nct_id: str | None = None) -> QueryFilters:
    match = _NCT_ID_PATTERN.search(query_text)
    resolved_nct_id = nct_id if nct_id is not None else (match.group(0).upper() if match else None)

    lowered = query_text.lower()
    resolved_doc_type = None
    for keyword, doc_type in _DOC_TYPE_KEYWORDS:
        if keyword in lowered:
            resolved_doc_type = doc_type
            break

    version_match = _VERSION_PATTERN.search(query_text)
    resolved_doc_version = float(version_match.group(1)) if version_match else None

    return QueryFilters(
        nct_id=resolved_nct_id,
        doc_type=resolved_doc_type,
        doc_version=resolved_doc_version,
    )


def filters_to_where_clause(filters: QueryFilters | None) -> tuple[str, list[Any]]:
    """SQL fragment (starting with " AND ", empty string if no filter is
    set) plus its bind params, for the optional (nct_id, doc_type,
    doc_version, section) filters -- shared by dense_search and
    lexical_search so both apply the identical prefilter semantics."""
    if filters is None:
        return "", []
    clauses = []
    params: list[Any] = []
    if filters.nct_id is not None:
        clauses.append("nct_id = %s")
        params.append(filters.nct_id)
    if filters.doc_type is not None:
        clauses.append("doc_type = %s")
        params.append(filters.doc_type)
    if filters.doc_version is not None:
        clauses.append("doc_version = %s")
        params.append(filters.doc_version)
    if filters.section is not None:
        clauses.append("section = %s")
        params.append(filters.section)
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params
