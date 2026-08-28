from protocol_drift.retrieval.query_parse import QueryFilters, parse_query_filters


def test_parses_explicit_nct_id_and_sap() -> None:
    filters = parse_query_filters(
        "What does the SAP for NCT03007407 say about the primary analysis?"
    )

    assert filters.nct_id == "NCT03007407"
    assert filters.doc_type == "sap"


def test_parses_statistical_analysis_plan_phrase() -> None:
    filters = parse_query_filters("What does the statistical analysis plan specify?")
    assert filters.doc_type == "sap"


def test_parses_protocol_keyword() -> None:
    filters = parse_query_filters("What does the protocol say about eligibility?")
    assert filters.doc_type == "protocol"


def test_parses_version_and_amendment_patterns() -> None:
    assert parse_query_filters("What changed in version 9?").doc_version == 9.0
    assert parse_query_filters("What changed in amendment 4.03?").doc_version == 4.03


def test_no_signal_returns_all_none_without_raising() -> None:
    filters = parse_query_filters("What is the primary outcome measure?")
    assert filters == QueryFilters(nct_id=None, doc_type=None, doc_version=None)


def test_explicit_nct_id_parameter_used_when_text_has_none() -> None:
    filters = parse_query_filters("What is the primary outcome?", nct_id="NCT00000001")
    assert filters.nct_id == "NCT00000001"


def test_explicit_nct_id_parameter_wins_over_text_match() -> None:
    # Caller context is authoritative -- a different NCT ID mentioned in
    # passing inside the question text should not override it.
    filters = parse_query_filters(
        "Unlike NCT09999999, this trial requires...", nct_id="NCT00000001"
    )
    assert filters.nct_id == "NCT00000001"


def test_nct_id_matched_case_insensitively_and_normalized_uppercase() -> None:
    filters = parse_query_filters("what does nct03007407 say?")
    assert filters.nct_id == "NCT03007407"
