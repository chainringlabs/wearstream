"""Check settings: validation, defaults and check names (CHECKS-SET-001, -002)."""

from __future__ import annotations

from typing import Any

import pytest
from checks_helpers import setting

from wearstream.checks import check_names, setting_problems
from wearstream.settings import load


# @spec CHECKS-SET-001
@pytest.mark.parametrize(
    ("fields", "families", "said"),
    [
        ({}, ("completeness",), "event_date_source"),
        ({"event_date_source": "payload"}, ("completeness",), "event_date_field"),
        ({}, ("freshness", "volume"), "volume"),
        ({"recent_folders": 0}, ("freshness",), "recent_folders"),
        ({"recent_folders": 61}, ("freshness",), "recent_folders"),
    ],
)
def test_invalid_settings_are_reported_naming_source_and_collection(
    fields: dict[str, Any], families: tuple[str, ...], said: str
) -> None:
    problems = setting_problems([setting("sleep", *families, **fields)])
    assert len(problems) == 1
    assert "whoop" in problems[0] and "sleep" in problems[0] and said in problems[0]


# @spec CHECKS-SET-001
def test_valid_settings_have_no_problems_and_the_reader_defaults_to_json() -> None:
    valid = [
        setting("sleep", "freshness", "completeness", event_date_source="partition"),
        setting("cycle", "completeness", event_date_source="payload", event_date_field="start"),
        setting("hr", "schema", "content", recent_folders=60),
        setting("rhr", "freshness", recent_folders=1),
    ]
    assert setting_problems(valid) == []
    assert valid[0].reader == "json"


# @spec CHECKS-SET-001
def test_configuration_validation_reports_a_sources_invalid_check_settings(
    env: dict[str, str], fake: Any
) -> None:
    fake.settings = [setting("sleep", "completeness")]
    for command in ("daemon", "run", "config"):
        loaded = load({**env, "WEARSTREAM_SOURCES": "whoop"}, command)
        assert any("sleep" in p and "event_date_source" in p for p in loaded.problems), command


# @spec CHECKS-SET-001
def test_a_source_not_in_this_build_adds_no_check_setting_problem(env: dict[str, str]) -> None:
    loaded = load({**env, "WEARSTREAM_SOURCES": "garmin"}, "config")
    assert not any("check" in p for p in loaded.problems)


# @spec CHECKS-SET-002
def test_collection_checks_are_named_source_collection_family() -> None:
    one = setting("sleep", "freshness", "completeness", "schema", "content")
    assert check_names(one) == [
        "whoop_sleep_freshness",
        "whoop_sleep_completeness",
        "whoop_sleep_schema_drift",
        "whoop_sleep_content_health",
    ]
