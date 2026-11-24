"""Job declarations, overrides and the schedule guardrails (SUPERVISOR-JOB)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sup_helpers import dirs

from wearstream import jobs
from wearstream.settings import load


def _problems(
    tmp_path: Path, sources: str = "whoop,garmin", command: str = "daemon", **settings: str
) -> list[str]:
    return load({**dirs(tmp_path), "WEARSTREAM_SOURCES": sources, **settings}, command).problems


# @spec SUPERVISOR-JOB-001
def test_declarations_with_their_defaults(tmp_path: Path) -> None:
    values = load({**dirs(tmp_path), "WEARSTREAM_SOURCES": "whoop,garmin"}, "daemon").values
    found = jobs.effective_jobs(values)
    whoop_range = found["whoop", "range"]
    assert (whoop_range.kind, whoop_range.schedule, whoop_range.window_days) == (
        "partitioned",
        "17 * * * *",
        8,
    )
    assert whoop_range.include_today is True
    assert (whoop_range.time_limit, whoop_range.backfill_pause) == (300, 60)
    assert found["whoop", "snapshots"].kind == "snapshot"
    assert found["whoop", "snapshots"].schedule == "17 * * * *"
    assert found["whoop", "checks"].kind == "checks"
    garmin_daily = found["garmin", "daily"]
    assert (garmin_daily.include_today, garmin_daily.window_days, garmin_daily.time_limit) == (
        False,
        14,
        900,
    )
    assert found["garmin", "reference"].kind == "snapshot"
    assert found["garmin", "checks"].schedule == "7 3-21/6 * * *"


# @spec SUPERVISOR-JOB-001
def test_only_enabled_sources_and_overrides_apply(tmp_path: Path) -> None:
    env = {
        **dirs(tmp_path), "WEARSTREAM_SOURCES": "garmin",
        "WEARSTREAM_GARMIN_DAILY_WINDOW_DAYS": "7", "WEARSTREAM_GARMIN_DAILY_TIME_LIMIT": "20",
        "WEARSTREAM_GARMIN_DAILY_SCHEDULE": "45 4 * * *",
        "WEARSTREAM_GARMIN_DAILY_BACKFILL_PAUSE": "90",
    }  # fmt: skip
    found = jobs.effective_jobs(load(env, "daemon").values)
    assert {source for source, _ in found} == {"garmin"}
    daily = found["garmin", "daily"]
    assert (daily.window_days, daily.time_limit, daily.schedule, daily.backfill_pause) == (
        7,
        1200,
        "45 4 * * *",
        90,
    )


# @spec SUPERVISOR-JOB-002
@pytest.mark.parametrize(
    "schedule", ["0 5 * * *", "* 5 * * *", "5,35 5 * * *", "*/30 * * * *", "1-5 * * * *"]
)
def test_the_minute_must_be_one_literal_minute(tmp_path: Path, schedule: str) -> None:
    problems = _problems(tmp_path, WEARSTREAM_GARMIN_DAILY_SCHEDULE=schedule)
    assert any("WEARSTREAM_GARMIN_DAILY_SCHEDULE" in p for p in problems)


# @spec SUPERVISOR-JOB-003
def test_the_defaults_pass_every_guardrail(tmp_path: Path) -> None:
    assert _problems(tmp_path) == []


# @spec SUPERVISOR-JOB-003
@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("WEARSTREAM_GARMIN_DAILY_SCHEDULE", "30 5 * * 1"),
        ("WEARSTREAM_WHOOP_CHECKS_SCHEDULE", "37 5 1 * *"),
        ("WEARSTREAM_GARMIN_DAILY_SCHEDULE", "30 5 30 2 *"),
        ("WEARSTREAM_GARMIN_DAILY_SCHEDULE", "30 25 * * *"),
    ],
)
def test_schedules_must_fire_at_least_daily(tmp_path: Path, setting: str, value: str) -> None:
    assert any(setting in p for p in _problems(tmp_path, **{setting: value}))


# @spec SUPERVISOR-JOB-003
def test_a_sources_work_must_fit_its_shortest_interval(tmp_path: Path) -> None:
    # Whoop: 8 days x 7 min + snapshots 5 + one backfill unit 7 = 68 min > the 60-minute interval.
    problems = _problems(tmp_path, "whoop", WEARSTREAM_WHOOP_RANGE_TIME_LIMIT="7")
    assert any("whoop" in p.lower() and "60" in p for p in problems)
    assert _problems(tmp_path, "whoop", WEARSTREAM_WHOOP_RANGE_TIME_LIMIT="6") == []
    # Garmin: an hourly daily job cannot fit 14 x 15 minutes.
    assert _problems(tmp_path, "garmin", WEARSTREAM_GARMIN_DAILY_SCHEDULE="30 * * * *") != []


# @spec SUPERVISOR-JOB-003
def test_a_checks_job_must_finish_within_its_interval(tmp_path: Path) -> None:
    problems = _problems(tmp_path, "whoop", WEARSTREAM_WHOOP_CHECKS_TIME_LIMIT="61")
    assert any("WEARSTREAM_WHOOP_CHECKS_TIME_LIMIT" in p for p in problems)


# @spec SUPERVISOR-JOB-003
def test_guardrails_ignore_disabled_sources_and_narrow_commands(tmp_path: Path) -> None:
    assert _problems(tmp_path, "whoop", WEARSTREAM_GARMIN_DAILY_SCHEDULE="30 * * * *") == []
    assert _problems(tmp_path, "whoop", "config", WEARSTREAM_WHOOP_RANGE_TIME_LIMIT="7") != []
    env = {**dirs(tmp_path), "WEARSTREAM_WHOOP_RANGE_TIME_LIMIT": "7"}
    assert load(env, "status").problems == []
