from protocol_drift.normalize.text import (
    contains_as_whole_token,
    extract_durations_in_months,
    normalize_text,
    strip_durations,
)


def test_normalize_text_casefolds_strips_punctuation_and_whitespace() -> None:
    assert normalize_text("  Phase 2, Study!  ") == "phase 2 study"


def test_contains_as_whole_token_matches_standalone_token() -> None:
    assert contains_as_whole_token("6", "target of 6 subjects")


def test_contains_as_whole_token_rejects_substring_of_longer_number() -> None:
    assert not contains_as_whole_token("6", "target of 16 subjects")


def test_contains_as_whole_token_empty_needle_is_false() -> None:
    assert not contains_as_whole_token("", "anything")


def test_extract_durations_in_months_converts_years() -> None:
    assert extract_durations_in_months("2 years") == {24.0}


def test_extract_durations_in_months_matches_the_discrepancy_definition_example() -> None:
    # discrepancy_definition.md's canonical "match, not divergence" example.
    assert extract_durations_in_months("24 months") == extract_durations_in_months("2 years")


def test_extract_durations_in_months_finds_multiple_and_ignores_non_durations() -> None:
    text = "Assessed at 6 months and again at 1 year; enrollment target is 50 subjects."
    assert extract_durations_in_months(text) == {6.0, 12.0}


def test_extract_durations_in_months_empty_for_no_duration() -> None:
    assert extract_durations_in_months("Bristol-Myers Squibb") == set()


def test_strip_durations_removes_duration_mention() -> None:
    assert strip_durations("Overall survival at 24 months") == "Overall survival at "


def test_strip_durations_leaves_text_with_no_duration_unchanged() -> None:
    assert strip_durations("Overall survival") == "Overall survival"
