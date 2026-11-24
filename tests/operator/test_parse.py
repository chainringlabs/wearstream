"""Parsing rules for setting values (OPERATOR-PARSE)."""

from __future__ import annotations

import datetime as dt

import pytest

from wearstream.settings import load
from wearstream.settings.parse import parse_bool, parse_cron, parse_date, parse_int, parse_list


# @spec OPERATOR-PARSE-001
def test_a_variable_set_to_an_empty_string_is_unset() -> None:
    loaded = load({"WEARSTREAM_LOG_FORMAT": "", "WEARSTREAM_SOURCES": "whoop"}, "config")
    assert loaded.values["WEARSTREAM_LOG_FORMAT"] == "console"
    assert loaded.origin["WEARSTREAM_LOG_FORMAT"] == "default"


# @spec OPERATOR-PARSE-002
@pytest.mark.parametrize(("raw", "value"), [("true", True), ("TRUE", True), ("False", False)])
def test_booleans_are_true_or_false_in_any_case(raw: str, value: bool) -> None:
    assert parse_bool(raw) is value


# @spec OPERATOR-PARSE-002
@pytest.mark.parametrize("raw", ["yes", "1", "on", "t"])
def test_other_boolean_spellings_are_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_bool(raw)


# @spec OPERATOR-PARSE-003
def test_lists_are_trimmed_lowercased_without_empties_or_duplicates() -> None:
    assert parse_list(" Whoop, garmin,,WHOOP , ") == ["whoop", "garmin"]


# @spec OPERATOR-PARSE-004
def test_schedules_are_five_field_cron() -> None:
    assert parse_cron("17 * * * *") == "17 * * * *"
    assert parse_cron("7 3-21/6 * * *") == "7 3-21/6 * * *"


# @spec OPERATOR-PARSE-004
@pytest.mark.parametrize(
    "raw", ["@daily", "17 * * *", "0 17 * * * *", "17 * * * mon?", "x * * * *"]
)
def test_other_schedule_forms_are_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_cron(raw)


# @spec OPERATOR-PARSE-004
def test_dates_are_iso_calendar_dates() -> None:
    assert parse_date("2024-01-01") == dt.date(2024, 1, 1)
    for raw in ("2024-1-1", "2024-02-30", "yesterday"):
        with pytest.raises(ValueError):
            parse_date(raw)


# @spec OPERATOR-PARSE-004
def test_numbers_are_decimal_integers() -> None:
    assert parse_int("60") == 60
    for raw in ("6.0", "0x10", "1e3", "-", "sixty"):
        with pytest.raises(ValueError):
            parse_int(raw)
