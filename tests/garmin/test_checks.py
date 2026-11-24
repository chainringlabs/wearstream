"""Garmin's check settings and the token-store health check (GARMIN-CAPTURE-CHECKS-001..007,
GARMIN-AUTH-HEALTH-001..003)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from garmin_helpers import FakeLibrary, write_session

from wearstream.checks import OwnCheck
from wearstream.garmin import GarminSource, catalog
from wearstream.settings import load, paths

Run = Callable[..., tuple[int, dict[str, Any]]]
IMPORTANT = ("sleep", "stress", "hrv", "respiration", "spo2", "resting_heart_rate", "heart_rates",
             "steps_intraday", "intensity_minutes", "training_status", "training_readiness",
             "max_metrics", "body_battery", "user_summary")  # fmt: skip
DEFAULT_EXPECTED_EMPTY = ("training_readiness", "body_battery_events", "running_tolerance", "goals",
                          "max_metrics", "hrv", "activities")  # fmt: skip


def values(env: dict[str, str], **settings: str) -> dict[str, Any]:
    loaded = load({**env, "WEARSTREAM_SOURCES": "garmin", **settings}, "run")
    assert loaded.problems == [], loaded.problems
    return loaded.values


def by_collection(env: dict[str, str], **settings: str) -> dict[str, Any]:
    return {s.collection: s for s in GarminSource().check_settings(values(env, **settings))}


# @spec GARMIN-CAPTURE-CHECKS-001, GARMIN-CAPTURE-CHECKS-005
def test_important_collections_get_every_check(env: dict[str, str]) -> None:
    found = by_collection(env)
    for name in IMPORTANT:
        s = found[name]
        assert set(s.families) == {"freshness", "completeness", "schema", "content"}, name
        assert (s.event_date_source, s.gap_to_now) == ("partition", True)
        assert (s.cadence_hours, s.grace_hours) == (26, 6)
        assert s.cadence_days == (14 if name in ("hrv", "training_readiness", "max_metrics") else 2)
        assert s.expected_empty is (name in ("hrv", "training_readiness", "max_metrics"))


# @spec GARMIN-CAPTURE-CHECKS-002, GARMIN-CAPTURE-CHECKS-006
def test_expected_empty_collections_get_freshness_and_content(env: dict[str, str]) -> None:
    found = by_collection(env)
    flagged = [e.name for e in catalog.CATALOG if e.skip_if_empty or e.skip_if_none]
    for name in [*flagged, *DEFAULT_EXPECTED_EMPTY]:
        if name in IMPORTANT:
            continue
        assert set(found[name].families) == {"freshness", "content"}, name
        assert found[name].expected_empty is True


# @spec GARMIN-CAPTURE-CHECKS-006
def test_the_operator_replaces_the_expected_empty_list(env: dict[str, str]) -> None:
    found = by_collection(env, WEARSTREAM_GARMIN_EXPECTED_EMPTY="floors")
    assert found["floors"].expected_empty is True
    assert set(found["goals"].families) == {"freshness"}
    assert found["lifestyle_logging"].expected_empty is True  # flagged: always
    nothing = by_collection(env, WEARSTREAM_GARMIN_EXPECTED_EMPTY="none")
    assert nothing["hrv"].expected_empty is True  # skip_if_none
    assert nothing["max_metrics"].cadence_days == 2


# @spec GARMIN-CAPTURE-CHECKS-003, GARMIN-CAPTURE-CHECKS-004
def test_everything_else_gets_freshness_and_activities_nothing(env: dict[str, str]) -> None:
    found = by_collection(env, WEARSTREAM_GARMIN_EXCLUDE="floors")
    assert set(found["devices"].families) == {"freshness"}
    assert set(found["weekly_steps"].families) == {"freshness"}
    assert "floors" not in found
    assert not set(found) & set(catalog.fan_out_collections(alt_formats=True))
    assert set(found) == {e.name for e in catalog.CATALOG} - {"floors"}


# @spec GARMIN-CAPTURE-CHECKS-007
def test_the_checks_job_runs_collection_checks_and_store_health(
    session: Path, garmin: FakeLibrary, run: Run, state: Path
) -> None:
    assert run("checks")[0] == 0
    rows = json.loads((state / "checks" / "garmin.json").read_text())["checks"]
    assert "garmin_tokenstore_health" in rows and "garmin_sleep_freshness" in rows
    assert garmin.clients == []


def health(env: dict[str, str]) -> OwnCheck:
    chosen = values(env)
    [check] = GarminSource().own_checks(chosen, paths(chosen))
    assert check.name == "garmin_tokenstore_health"
    return check


# @spec GARMIN-AUTH-HEALTH-003
def test_a_healthy_store_passes(env: dict[str, str], session: Path) -> None:
    assert health(env).run(None).severity == "pass"


# @spec GARMIN-AUTH-HEALTH-002
@pytest.mark.parametrize("damage", ["missing", "not_json", "missing_session_fields"])
def test_a_missing_or_invalid_token_file_fails(
    env: dict[str, str], folder: Path, damage: str
) -> None:
    if damage == "not_json":
        (folder / "garmin_tokens.json").write_text("{")
    elif damage == "missing_session_fields":
        write_session(folder, {"di_token": "x", "di_refresh_token": "y"})
    result = health(env).run(None)
    assert (result.severity, result.details["reason"]) == ("error", damage)
    assert result.action and result.details["path"]


# @spec GARMIN-AUTH-HEALTH-002
def test_a_missing_token_file_is_suppressed_while_waiting(env: dict[str, str]) -> None:
    result = health(env).run("waiting_for_auth")
    assert result.suppressed == "waiting_for_auth"


# @spec GARMIN-AUTH-HEALTH-001
def test_an_unusable_store_fails_even_while_waiting(
    env: dict[str, str], session: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wearstream.garmin.store.is_mount", lambda _p: False)
    result = health(env).run("waiting_for_auth")
    assert (result.severity, result.suppressed, result.details["reason"]) == (
        "error", None, "off_volume",
    )  # fmt: skip
    assert "DI-TOKEN-1" not in json.dumps(result.details)
