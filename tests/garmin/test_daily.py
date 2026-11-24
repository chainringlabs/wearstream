"""The `daily` unit: what it calls, in what order, and what it writes
(GARMIN-CAPTURE-DAILY-001..005, -WRITE-001..005, -DEDUPE-001..003, -CAT-003, -CAT-004)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from garmin_helpers import FakeClock, FakeLibrary

from wearstream.capture import list_captures
from wearstream.garmin import catalog

Run = Callable[..., tuple[int, dict[str, Any]]]
D = "2026-11-20"
PER_ACTIVITY = [method for _name, method in catalog.PER_ACTIVITY]


def stored(data: Path, collection: str, day: str = D) -> list[Any]:
    return list_captures(data, "garmin", collection, day)


def activities(*ids: int) -> list[dict[str, Any]]:
    return [{"activityId": i, "activityName": f"ride {i}"} for i in ids]


# @spec GARMIN-CAPTURE-DAILY-001
def test_daily_range_and_device_entries_are_called_in_catalog_order(
    session: Path, garmin: FakeLibrary, run: Run
) -> None:
    assert run()[0] == 0
    daily = [e.method for e in catalog.CATALOG if e.kind == "daily"]
    ranged = [e.method for e in catalog.CATALOG if e.kind == "range" and e.name != "activities"]
    per_device = [e.method for e in catalog.CATALOG if e.kind == "per_device_range"]
    expected = ["get_devices", *daily, *ranged, *per_device, "get_activities_by_date"]
    assert garmin.methods() == expected
    calls = {name: (args, kwargs) for name, args, kwargs, _t in garmin.calls}
    assert calls["get_sleep_data"] == ((D,), {})
    assert calls["get_body_battery"] == ((D, D), {})
    assert calls["get_device_solar_data"] == (("111", D, D), {})
    assert calls["get_activities_by_date"] == ((D, D), {})


# @spec GARMIN-CAPTURE-DAILY-002, GARMIN-CAPTURE-DAILY-003
def test_each_activity_fans_out_then_downloads_its_fit_file(
    session: Path, garmin: FakeLibrary, run: Run, data: Path
) -> None:
    garmin.answers["get_activities_by_date"] = activities(9, 10)
    assert run()[0] == 0
    fan_out = garmin.methods()[garmin.methods().index("get_activities_by_date") + 1 :]
    assert fan_out == [*PER_ACTIVITY, "download_activity"] * 2
    downloads = [(a, k) for n, a, k, _t in garmin.calls if n == "download_activity"]
    assert [(args[0], args[1].name) for args, _k in downloads] == [
        ("9", "ORIGINAL"),
        ("10", "ORIGINAL"),
    ]
    for collection in ("sleep", "body_battery", "device_solar", "activities", "activity_summary"):
        assert stored(data, collection), collection
    [fit, _other] = stored(data, "activity_fit")
    assert fit.payload.suffix == ".zip"


# @spec GARMIN-CAPTURE-CAT-003
def test_deselected_entries_are_never_called(session: Path, garmin: FakeLibrary, run: Run) -> None:
    garmin.answers["get_activities_by_date"] = activities(9)
    run(WEARSTREAM_GARMIN_EXCLUDE="sleep,device_solar,activity_weather")
    called = garmin.methods()
    assert "get_sleep_data" not in called and "get_activity_weather" not in called
    assert "get_devices" not in called  # nothing selected needs a device


# @spec GARMIN-CAPTURE-CAT-004
def test_alternate_formats_when_enabled(
    session: Path, garmin: FakeLibrary, run: Run, data: Path
) -> None:
    garmin.answers["get_activities_by_date"] = activities(9)
    garmin.answers["download_activity"] = lambda aid, fmt: f"{fmt.name}-bytes".encode()
    run(WEARSTREAM_GARMIN_ALT_FORMATS="true")
    formats = [a[1].name for n, a, _k, _t in garmin.calls if n == "download_activity"]
    assert formats == ["ORIGINAL", "TCX", "GPX", "KML", "CSV"]
    for collection, ext in (("activity_tcx", ".tcx"), ("activity_gpx", ".gpx"),
                            ("activity_kml", ".kml"), ("activity_csv", ".csv")):  # fmt: skip
        [capture] = stored(data, collection)
        assert capture.payload.suffix == ext


# @spec GARMIN-CAPTURE-DAILY-004
def test_a_failed_device_discovery_fails_only_device_entries(
    session: Path, garmin: FakeLibrary, run: Run
) -> None:
    garmin.answers["get_devices"] = RuntimeError("boom")
    code, result = run()
    assert code == 1
    assert "device_solar" in result["error"]
    assert "get_device_solar_data" not in garmin.methods()
    assert "get_activities_by_date" in garmin.methods()


# @spec GARMIN-CAPTURE-DAILY-004
def test_a_failed_activity_list_skips_the_fan_out(
    session: Path, garmin: FakeLibrary, run: Run
) -> None:
    garmin.answers["get_activities_by_date"] = RuntimeError("boom")
    code, result = run()
    assert code == 1 and "activities" in result["error"]
    assert garmin.methods()[-1] == "get_activities_by_date"


# @spec GARMIN-CAPTURE-DAILY-005
@pytest.mark.parametrize(("setting", "pause"), [({}, 2), ({"WEARSTREAM_GARMIN_PACING": "5"}, 5)])
def test_calls_are_paced(
    session: Path, garmin: FakeLibrary, clock: FakeClock, run: Run, setting: dict, pause: int
) -> None:
    run(**setting)
    starts = [t for *_rest, t in garmin.calls]
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert gaps and min(gaps) >= pause


# @spec GARMIN-CAPTURE-WRITE-001, GARMIN-CAPTURE-WRITE-002, GARMIN-CAPTURE-WRITE-003
def test_none_and_flagged_empties_are_not_captured(
    session: Path, garmin: FakeLibrary, run: Run, data: Path
) -> None:
    garmin.answers["get_sleep_data"] = None
    garmin.answers["get_hrv_data"] = None
    garmin.answers["get_lifestyle_logging_data"] = []
    garmin.answers["get_floors"] = []
    garmin.answers["get_hydration_data"] = {}
    assert run()[0] == 0
    assert stored(data, "sleep") == [] and stored(data, "hrv") == []
    assert stored(data, "lifestyle_logging") == []
    assert json.loads(stored(data, "floors")[0].payload.read_bytes()) == []
    assert json.loads(stored(data, "hydration")[0].payload.read_bytes()) == {}


# @spec GARMIN-CAPTURE-WRITE-004, GARMIN-CAPTURE-WRITE-005
def test_what_a_capture_holds(session: Path, garmin: FakeLibrary, run: Run, data: Path) -> None:
    garmin.answers["get_stress_data"] = None
    garmin.answers["get_all_day_stress"] = {"z": 1, "a": "é"}
    garmin.answers["get_activities_by_date"] = activities(9)
    run()
    [stress] = stored(data, "stress")
    assert stress.payload.read_bytes() == '{"z":1,"a":"é"}'.encode()
    assert (stress.meta["capture_mode"], stress.meta["request_url"]) == (
        "reserialized",
        "get_all_day_stress",
    )
    assert (stress.meta["request_params"], stress.meta["http_status"]) == ({"cdate": D}, 200)
    [fit] = stored(data, "activity_fit")
    assert fit.payload.read_bytes() == b"PK\x03\x04fit-bytes"
    assert fit.meta["capture_mode"] == "raw"
    assert fit.meta["request_params"] == {"activity_id": 9, "format": "ORIGINAL"}
    assert stored(data, "device_solar")[0].meta["request_params"] == {
        "device_id": 111,
        "start": D,
        "end": D,
    }
    assert stored(data, "body_battery")[0].meta["request_params"] == {"start": D, "end": D}
    assert stored(data, "activity_summary")[0].meta["request_params"] == {"activity_id": 9}


# @spec GARMIN-CAPTURE-DEDUPE-001
def test_single_request_folders_skip_an_unchanged_answer(
    session: Path, garmin: FakeLibrary, run: Run, data: Path
) -> None:
    run()
    run()
    for collection in ("sleep", "body_battery", "activities"):
        assert len(stored(data, collection)) == 1, collection


# @spec GARMIN-CAPTURE-DEDUPE-002
def test_requests_sharing_a_folder_are_compared_per_request(
    session: Path, garmin: FakeLibrary, run: Run, data: Path
) -> None:
    garmin.answers["get_activities_by_date"] = activities(9, 10)
    garmin.answers["get_activity_weather"] = []
    run()
    assert len(stored(data, "activity_weather")) == 2
    run()
    assert len(stored(data, "activity_weather")) == 2


# @spec GARMIN-CAPTURE-DEDUPE-003
def test_flagged_entries_keep_every_capture(
    session: Path, garmin: FakeLibrary, run: Run, data: Path
) -> None:
    garmin.answers["get_device_solar_data"] = [{"solar": 1}]
    garmin.answers["get_hrv_data"] = {"hrv": 1}
    run()
    run()
    assert len(stored(data, "device_solar")) == 2
    assert len(stored(data, "hrv")) == 2
