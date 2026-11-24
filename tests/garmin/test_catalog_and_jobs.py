"""Garmin's jobs, its catalog and selection (GARMIN-CAPTURE-JOB-001..003, -CAT-001..003,
OPERATOR-SET-007)."""

from __future__ import annotations

from typing import Any

import garminconnect
import pytest

from wearstream.garmin import GarminSource, catalog
from wearstream.jobs import effective_jobs
from wearstream.settings import load


def values(env: dict[str, str], **settings: str) -> dict[str, Any]:
    loaded = load({**env, "WEARSTREAM_SOURCES": "garmin", **settings}, "run")
    assert loaded.problems == [], loaded.problems
    return loaded.values


def problems(env: dict[str, str], **settings: str) -> list[str]:
    return load({**env, "WEARSTREAM_SOURCES": "garmin", **settings}, "config").problems


# @spec GARMIN-CAPTURE-JOB-001, GARMIN-CAPTURE-JOB-002, GARMIN-CAPTURE-JOB-003
def test_the_declared_jobs(env: dict[str, str]) -> None:
    jobs = effective_jobs(values(env))
    daily, reference, checks = (jobs["garmin", n] for n in ("daily", "reference", "checks"))
    assert (daily.kind, daily.schedule, daily.window_days, daily.include_today) == (
        "partitioned", "30 5 * * *", 14, False,
    )  # fmt: skip
    assert (daily.time_limit, daily.backfill_pause) == (900, 60)
    assert (reference.kind, reference.schedule, reference.time_limit) == (
        "snapshot",
        "30 5 * * *",
        600,
    )
    assert (checks.kind, checks.schedule, checks.time_limit) == ("checks", "7 3-21/6 * * *", 600)
    for setting in (
        {"WEARSTREAM_GARMIN_DAILY_TIME_LIMIT": "4"},
        {"WEARSTREAM_GARMIN_REFERENCE_TIME_LIMIT": "4"},
        {"WEARSTREAM_GARMIN_CHECKS_TIME_LIMIT": "2"},
        {"WEARSTREAM_GARMIN_DAILY_BACKFILL_PAUSE": "29"},
    ):
        assert any(next(iter(setting)) in p for p in problems(env, **setting))


# @spec GARMIN-CAPTURE-JOB-001, GARMIN-CAPTURE-CAT-003
def test_the_daily_jobs_collections_are_what_a_daily_unit_writes(env: dict[str, str]) -> None:
    found = GarminSource().collections("daily", values(env))
    assert {
        "sleep",
        "stress",
        "activities",
        "device_solar",
        "activity_summary",
        "activity_fit",
    } <= set(found)
    assert not {"devices", "goals", "weekly_steps", "activity_tcx"} & set(found)
    narrowed = GarminSource().collections(
        "daily", values(env, WEARSTREAM_GARMIN_EXCLUDE="sleep,activities")
    )
    assert "sleep" not in narrowed and not [c for c in narrowed if c.startswith("activity")]
    assert "activity_tcx" in GarminSource().collections(
        "daily", values(env, WEARSTREAM_GARMIN_ALT_FORMATS="true")
    )
    assert GarminSource().collections("reference", values(env)) == ()


# @spec GARMIN-CAPTURE-CAT-001
def test_the_catalog_never_mutates_or_signs_in() -> None:
    assert catalog.forbidden() == []
    for entry in catalog.CATALOG:
        assert hasattr(garminconnect.Garmin, entry.method), entry.method


# @spec GARMIN-CAPTURE-CAT-001
def test_every_readable_library_method_has_been_reviewed() -> None:
    """A library upgrade that adds a readable method fails here until it is reviewed."""
    assert catalog.uncovered(garminconnect.Garmin) == []


# @spec GARMIN-CAPTURE-CAT-002
@pytest.mark.parametrize(
    ("select", "exclude", "name", "chosen"),
    [
        ([], [], "sleep", True),
        (["sleep"], [], "stress", False),
        (["sleep"], ["sleep"], "sleep", False),
        ([], ["activities"], "activity_fit", False),
        ([], ["activities"], "activity_summary", False),
        ([], ["activity_fit"], "activity_summary", True),
        (["activities"], [], "activity_weather", False),
        (["activities", "activity_weather"], [], "activity_weather", True),
        (["activity_weather"], [], "activity_weather", False),
    ],
)
def test_selection(select: list[str], exclude: list[str], name: str, chosen: bool) -> None:
    settings = {"WEARSTREAM_GARMIN_SELECT": select, "WEARSTREAM_GARMIN_EXCLUDE": exclude}
    assert catalog.selected(name, settings) is chosen


# @spec OPERATOR-SET-007
def test_names_that_are_not_garmin_collections_are_invalid(env: dict[str, str]) -> None:
    assert problems(env, WEARSTREAM_GARMIN_SELECT="sleep,activity_fit,activity_tcx") == []
    for name in (
        "WEARSTREAM_GARMIN_SELECT",
        "WEARSTREAM_GARMIN_EXCLUDE",
        "WEARSTREAM_GARMIN_EXPECTED_EMPTY",
    ):
        found = problems(env, **{name: "sleep,sleeep"})
        assert any(name in p and "sleeep" in p for p in found), found
    assert problems(env, WEARSTREAM_GARMIN_EXPECTED_EMPTY="none") == []
    assert (
        values(env, WEARSTREAM_GARMIN_EXPECTED_EMPTY="none")["WEARSTREAM_GARMIN_EXPECTED_EMPTY"]
        == []
    )
