"""The `reference` unit (GARMIN-CAPTURE-REF-001, -002, -WRITE-006)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

from garmin_helpers import FakeLibrary

from wearstream.capture import list_captures
from wearstream.garmin import catalog

Run = Callable[..., tuple[int, dict[str, Any]]]
REFERENCE_KINDS = ("static", "static_goals", "weekly", "per_profile", "per_device")


def today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()  # the capture writer's UTC fetch date


def stored(data: Path, collection: str) -> list[Any]:
    return list_captures(data, "garmin", collection, today())


# @spec GARMIN-CAPTURE-REF-001
def test_reference_entries_are_called_in_catalog_order(
    session: Path, garmin: FakeLibrary, run: Run, data: Path
) -> None:
    assert run("reference")[0] == 0
    called = garmin.methods()
    assert called[:2] == ["get_devices", "get_userprofile_settings"]
    expected = []
    for entry in catalog.CATALOG:
        if entry.kind in REFERENCE_KINDS:
            expected += [entry.method] * (
                len(catalog.GOAL_STATUSES) if entry.kind == "static_goals" else 1
            )
    assert called[2:] == expected
    calls = [(n, a, k) for n, a, k, _t in garmin.calls]
    assert ("get_weekly_steps", ("2026-11-24", 4), {}) in calls
    assert [k["status"] for n, _a, k in calls if n == "get_goals"] == list(catalog.GOAL_STATUSES)
    assert ("get_gear", (777,), {}) in calls
    assert ("get_device_settings", (111,), {}) in calls
    goals = stored(data, "goals")
    assert sorted(c.meta["request_params"]["status"] for c in goals) == sorted(
        catalog.GOAL_STATUSES
    )
    assert stored(data, "gear")[0].meta["request_params"] == {"user_profile_number": 777}
    assert stored(data, "weekly_steps")[0].meta["request_params"] == {
        "end": "2026-11-24",
        "weeks": 4,
    }


# @spec GARMIN-CAPTURE-REF-002
def test_a_failed_discovery_fails_only_its_dependents(
    session: Path, garmin: FakeLibrary, run: Run
) -> None:
    garmin.answers["get_devices"] = RuntimeError("boom")
    garmin.answers["get_userprofile_settings"] = RuntimeError("boom")
    code, result = run("reference")
    assert code == 1
    for name in ("device_settings", "gear", "gear_defaults", "userprofile_settings"):
        assert name in result["error"]
    called = garmin.methods()
    assert "get_device_settings" not in called and "get_gear" not in called
    assert "get_race_predictions" in called


# @spec GARMIN-CAPTURE-WRITE-006
def test_screened_entries_lose_credential_keys(
    session: Path, garmin: FakeLibrary, run: Run, data: Path
) -> None:
    garmin.answers["get_user_profile"] = {"id": 1, "userData": {"authToken": "T", "gender": "x"}}
    run("reference")
    [capture] = stored(data, "user_settings")
    assert b"authToken" not in capture.payload.read_bytes()
    assert capture.meta["redacted_fields"] == ["userData.authToken"]
