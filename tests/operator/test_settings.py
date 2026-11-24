"""Settings: sources, overrides, bounds, unknown and retired names, umask (OPERATOR-SET)."""

from __future__ import annotations

import datetime as dt
import os

import pytest

from wearstream.settings import REGISTRY, apply_umask, load

BASE = {"WEARSTREAM_SOURCES": "whoop,garmin"}


def _problems(env: dict[str, str], command: str = "daemon", **kw: str) -> list[str]:
    return load({**BASE, **env}, command, **kw).problems


# @spec OPERATOR-SET-001
def test_daemon_and_run_require_sources() -> None:
    for command in ("daemon", "run"):
        assert any("WEARSTREAM_SOURCES" in p for p in load({}, command).problems)
    assert load({}, "status").problems == []


# @spec OPERATOR-SET-001
def test_sources_accept_only_whoop_and_garmin() -> None:
    assert load({"WEARSTREAM_SOURCES": "Garmin, whoop"}, "daemon").values["WEARSTREAM_SOURCES"] == [
        "garmin",
        "whoop",
    ]
    assert any("fitbit" in p for p in load({"WEARSTREAM_SOURCES": "fitbit"}, "daemon").problems)


# @spec OPERATOR-SET-002
def test_defaults_come_from_the_registry() -> None:
    values = load(BASE, "daemon").values
    assert values["WEARSTREAM_WHOOP_SCHEDULE"] == "17 * * * *"
    assert values["WEARSTREAM_WHOOP_RANGE_TIME_LIMIT"] == 5
    assert values["WEARSTREAM_GARMIN_DAILY_WINDOW_DAYS"] == 14
    assert values["WEARSTREAM_GARMIN_CHECKS_SCHEDULE"] == "7 3-21/6 * * *"
    assert values["WEARSTREAM_FAILING_TICKS"] == 3


# @spec OPERATOR-SET-002
@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("WEARSTREAM_WHOOP_RANGE_TIME_LIMIT", "2"),
        ("WEARSTREAM_GARMIN_DAILY_TIME_LIMIT", "4"),
        ("WEARSTREAM_GARMIN_CHECKS_TIME_LIMIT", "2"),
        ("WEARSTREAM_GARMIN_DAILY_TIME_LIMIT", "361"),
        ("WEARSTREAM_WHOOP_RANGE_WINDOW_DAYS", "31"),
        ("WEARSTREAM_GARMIN_DAILY_WINDOW_DAYS", "0"),
        ("WEARSTREAM_WHOOP_RANGE_BACKFILL_PAUSE", "29"),
        ("WEARSTREAM_GARMIN_DAILY_SCHEDULE", "0 5 * * *"),
        ("WEARSTREAM_WHOOP_CHECKS_SCHEDULE", "*/5 * * * *"),
    ],
)
def test_overrides_outside_their_bounds_are_invalid(name: str, bad: str) -> None:
    assert any(name in p for p in _problems({name: bad}))


# @spec OPERATOR-SET-002
@pytest.mark.parametrize("minute", ["55", "59", "0", "5"])
def test_whoop_schedule_minute_may_not_be_55_to_5(minute: str) -> None:
    assert any(
        "WEARSTREAM_WHOOP_SCHEDULE" in p
        for p in _problems({"WEARSTREAM_WHOOP_SCHEDULE": f"{minute} * * * *"})
    )
    assert not _problems({"WEARSTREAM_WHOOP_SCHEDULE": "6 * * * *"})


# @spec OPERATOR-SET-003
def test_an_override_a_job_does_not_take_is_invalid() -> None:
    problems = _problems({"WEARSTREAM_WHOOP_SNAPSHOTS_WINDOW_DAYS": "3"})
    assert any("WEARSTREAM_WHOOP_SNAPSHOTS_WINDOW_DAYS" in p and "does not" in p for p in problems)


# @spec OPERATOR-SET-004
def test_a_disabled_sources_settings_are_ignored_by_the_daemon() -> None:
    env = {"WEARSTREAM_SOURCES": "whoop", "WEARSTREAM_GARMIN_DAILY_TIME_LIMIT": "1"}
    loaded = load(env, "daemon")
    assert loaded.problems == []
    assert loaded.ignored == ["WEARSTREAM_GARMIN_DAILY_TIME_LIMIT"]


# @spec OPERATOR-SET-004, OPERATOR-SET-009
def test_auth_reads_its_source_whatever_sources_says() -> None:
    env = {"WEARSTREAM_SOURCES": "whoop", "WEARSTREAM_GARMIN_DAILY_TIME_LIMIT": "20"}
    loaded = load(env, "auth", source="garmin")
    assert loaded.problems == []
    assert loaded.values["WEARSTREAM_GARMIN_DAILY_TIME_LIMIT"] == 20
    assert "WEARSTREAM_WHOOP_RANGE_TIME_LIMIT" not in loaded.values


# @spec OPERATOR-SET-005
def test_retired_settings_are_ignored_and_reported() -> None:
    loaded = load(
        {**BASE, "WEARSTREAM_OLD_KNOB": "1"}, "daemon", retired=frozenset({"WEARSTREAM_OLD_KNOB"})
    )
    assert loaded.problems == []
    assert loaded.retired == ["WEARSTREAM_OLD_KNOB"]


# @spec OPERATOR-SET-006
def test_unknown_wearstream_variables_are_invalid() -> None:
    assert any("WEARSTREAM_LOG_FROMAT" in p for p in _problems({"WEARSTREAM_LOG_FROMAT": "json"}))
    assert any(
        "WEARSTREAM_LOG_FROMAT" in p
        for p in load({"WEARSTREAM_LOG_FROMAT": "x"}, "status").problems
    )


# @spec OPERATOR-SET-008
@pytest.mark.parametrize(
    ("name", "bad"),
    [
        (
            "WEARSTREAM_WHOOP_BACKFILL_FROM",
            (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)).isoformat(),
        ),
        ("WEARSTREAM_WHOOP_REQUESTS_PER_MINUTE", "91"),
        ("WEARSTREAM_WHOOP_REQUESTS_PER_MINUTE", "0"),
        ("WEARSTREAM_GARMIN_PACING", "1"),
        ("WEARSTREAM_FAILING_TICKS", "101"),
        ("WEARSTREAM_LOG_LEVEL", "WARNING"),
    ],
)
def test_other_bounded_settings(name: str, bad: str) -> None:
    assert any(name in p for p in _problems({name: bad}))


# @spec OPERATOR-SET-009
def test_each_command_reads_only_its_settings() -> None:
    env = {**BASE, "WEARSTREAM_WHOOP_RANGE_TIME_LIMIT": "1"}
    assert load(env, "status").problems == []
    assert load(env, "healthcheck").problems == []
    accept = load({"WEARSTREAM_GARMIN_SELECT": "sleep"}, "accept-schema", source="garmin")
    assert accept.values["WEARSTREAM_GARMIN_SELECT"] == ["sleep"]
    assert "WEARSTREAM_GARMIN_CHECKS_TIME_LIMIT" in accept.values
    assert "WEARSTREAM_GARMIN_DAILY_TIME_LIMIT" not in accept.values


# @spec OPERATOR-SET-010
@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        ("022", True),
        ("0077", True),
        ("077", True),
        ("078", False),
        ("100", False),
        ("22", False),
        ("0o22", False),
    ],
)
def test_umask_values(raw: str, ok: bool) -> None:
    assert (not _problems({"WEARSTREAM_UMASK": raw})) is ok


# @spec OPERATOR-SET-010
def test_apply_umask_sets_the_process_mask() -> None:
    previous = os.umask(0o022)
    try:
        apply_umask("077")
        assert os.umask(0o022) == 0o077
    finally:
        os.umask(previous)


def test_every_registry_entry_is_a_wearstream_variable_with_a_valid_default() -> None:
    assert all(name.startswith("WEARSTREAM_") for name in REGISTRY)
    assert load(BASE, "config").problems == []
