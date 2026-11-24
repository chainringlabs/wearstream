"""Whoop's jobs, its collection check settings and token health (WHOOP-CAPTURE-JOB-001..004,
WHOOP-CAPTURE-CHECKS-001..006, WHOOP-AUTH-HEALTH-001..003)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from whoop_helpers import FakeClock, FakeWhoop, Logs, minutes, write_token

from wearstream.checks import OwnCheck
from wearstream.jobs import effective_jobs
from wearstream.settings import load, paths
from wearstream.whoop import WhoopSource

Run = Callable[..., tuple[int, dict[str, Any]]]
RANGE = ("sleep", "recovery", "workout", "cycle")


def values(env: dict[str, str], **settings: str) -> dict[str, Any]:
    loaded = load({**env, "WEARSTREAM_SOURCES": "whoop", **settings}, "run")
    assert loaded.problems == [], loaded.problems
    return loaded.values


def problems(env: dict[str, str], **settings: str) -> list[str]:
    return load({**env, "WEARSTREAM_SOURCES": "whoop", **settings}, "config").problems


# @spec WHOOP-CAPTURE-JOB-001, WHOOP-CAPTURE-JOB-002, WHOOP-CAPTURE-JOB-003
def test_the_declared_jobs(env: dict[str, str]) -> None:
    jobs = effective_jobs(values(env))
    range_, snapshots, checks = (
        jobs["whoop", "range"],
        jobs["whoop", "snapshots"],
        jobs["whoop", "checks"],
    )
    assert (range_.kind, range_.schedule, range_.window_days, range_.include_today) == (
        "partitioned", "17 * * * *", 8, True,
    )  # fmt: skip
    assert (range_.time_limit, range_.backfill_pause) == (300, 60)
    assert (snapshots.kind, snapshots.schedule, snapshots.time_limit) == (
        "snapshot",
        "17 * * * *",
        300,
    )
    assert (checks.kind, checks.schedule, checks.time_limit) == ("checks", "37 * * * *", 600)
    assert WhoopSource().collections("range", values(env)) == RANGE


# @spec WHOOP-CAPTURE-JOB-001, WHOOP-CAPTURE-JOB-002, WHOOP-CAPTURE-JOB-003
@pytest.mark.parametrize(
    "setting",
    [
        {"WEARSTREAM_WHOOP_RANGE_TIME_LIMIT": "2"},
        {"WEARSTREAM_WHOOP_SNAPSHOTS_TIME_LIMIT": "2"},
        {"WEARSTREAM_WHOOP_CHECKS_TIME_LIMIT": "2"},
        {"WEARSTREAM_WHOOP_RANGE_BACKFILL_PAUSE": "29"},
    ],
)
def test_minimums(env: dict[str, str], setting: dict[str, str]) -> None:
    assert any(next(iter(setting)) in p for p in problems(env, **setting))


# @spec WHOOP-CAPTURE-JOB-004
def test_range_and_snapshots_share_one_schedule_away_from_the_top_of_the_hour(
    env: dict[str, str],
) -> None:
    jobs = effective_jobs(values(env, WEARSTREAM_WHOOP_SCHEDULE="23 * * * *"))
    assert jobs["whoop", "range"].schedule == jobs["whoop", "snapshots"].schedule == "23 * * * *"
    for minute in ("55", "59", "0", "5"):
        assert any(
            "WEARSTREAM_WHOOP_SCHEDULE" in p
            for p in problems(env, WEARSTREAM_WHOOP_SCHEDULE=f"{minute} * * * *")
        )


def settings_by_collection(env: dict[str, str]) -> dict[str, Any]:
    return {s.collection: s for s in WhoopSource().check_settings(values(env))}


# @spec WHOOP-CAPTURE-CHECKS-001, WHOOP-CAPTURE-CHECKS-005, WHOOP-CAPTURE-CHECKS-006
def test_daily_collections_get_every_check(env: dict[str, str]) -> None:
    found = settings_by_collection(env)
    for collection, field in (("sleep", "start"), ("recovery", "created_at"), ("cycle", "start")):
        s = found[collection]
        assert set(s.families) == {"freshness", "completeness", "schema", "content"}
        assert (s.cadence_hours, s.grace_hours, s.cadence_days) == (26, 6, 2)
        assert (s.event_date_source, s.event_date_field) == ("payload", field)
        assert s.unnest_records is True and s.recent_folders == 14


# @spec WHOOP-CAPTURE-CHECKS-002, WHOOP-CAPTURE-CHECKS-006
def test_workouts_get_completeness_with_gap_to_now_and_schema_only(env: dict[str, str]) -> None:
    s = settings_by_collection(env)["workout"]
    assert set(s.families) == {"completeness", "schema"}
    assert (s.cadence_days, s.gap_to_now, s.event_date_field) == (14, True, "start")
    assert s.unnest_records is True


# @spec WHOOP-CAPTURE-CHECKS-003, WHOOP-CAPTURE-CHECKS-006
def test_snapshots_get_freshness_schema_and_content(env: dict[str, str]) -> None:
    found = settings_by_collection(env)
    for collection in ("profile", "body_measurement"):
        s = found[collection]
        assert set(s.families) == {"freshness", "schema", "content"}
        assert (s.cadence_hours, s.grace_hours, s.unnest_records) == (26, 6, False)
    assert set(found) == {*RANGE, "profile", "body_measurement"}


# @spec WHOOP-CAPTURE-CHECKS-004, WHOOP-CAPTURE-CHECKS-005
def test_the_checks_job_runs_the_collection_checks_and_token_health(
    token: dict, whoop: FakeWhoop, run: Run, state: Path, data: Path, logs: Logs
) -> None:
    assert run("checks")[0] == 0
    rows = json.loads((state / "checks" / "whoop.json").read_text())["checks"]
    assert "whoop_token_health" in rows and "whoop_sleep_freshness" in rows
    assert "whoop_workout_completeness" in rows
    assert all(
        e["level"] != "error" for e in logs.events("check_failed") if "completeness" in e["check"]
    )
    assert whoop.requests == []


def health(env: dict[str, str], **settings: str) -> OwnCheck:
    chosen = values(env, **settings)
    [check] = WhoopSource().own_checks(chosen, paths(chosen))
    return check


# @spec WHOOP-AUTH-HEALTH-001, WHOOP-AUTH-HEALTH-003
@pytest.mark.parametrize(("expired_minutes_ago", "severity"), [(89, "pass"), (91, "error")])
def test_token_health_fails_when_the_token_is_too_old(
    folder: Path, clock: FakeClock, env: dict[str, str], whoop: FakeWhoop,
    expired_minutes_ago: int, severity: str,
) -> None:  # fmt: skip
    write_token(folder, clock, expires_at=minutes(clock, -expired_minutes_ago))
    check = health(env)
    assert isinstance(check, OwnCheck) and check.name == "whoop_token_health"
    result = check.run(None)
    assert result.severity == severity
    if severity == "error":
        assert "wearstream auth whoop" in result.action
    assert whoop.requests == []


# @spec WHOOP-AUTH-HEALTH-001
def test_the_threshold_follows_the_range_schedule(
    folder: Path, clock: FakeClock, env: dict[str, str]
) -> None:
    write_token(folder, clock, expires_at=minutes(clock, -140))
    assert health(env, WEARSTREAM_WHOOP_SCHEDULE="17 */2 * * *").run(None).severity == "pass"


# @spec WHOOP-AUTH-HEALTH-001
@pytest.mark.parametrize("damage", ["missing", "unreadable"])
def test_a_missing_or_unreadable_token_fails_health(
    folder: Path, env: dict[str, str], damage: str
) -> None:
    if damage == "unreadable":
        (folder / "token.json").write_text("{")
    assert health(env).run(None).severity == "error"


# @spec WHOOP-AUTH-HEALTH-002
def test_token_health_is_suppressed_while_capture_cannot_happen(
    folder: Path, clock: FakeClock, env: dict[str, str]
) -> None:
    write_token(folder, clock, expires_at=minutes(clock, -500))
    result = health(env).run("waiting_for_auth")
    assert result.suppressed == "waiting_for_auth"
