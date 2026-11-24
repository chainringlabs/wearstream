"""`wearstream accept-schema` (CHECKS-ACCEPT-001..004)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from checks_helpers import Logs, put, read_state, setting, write_state

import wearstream.checks.accept as accept
from wearstream import cli, jobs, log
from wearstream.checks import SIGNATURE_VERSION, run_unit
from wearstream.state import release, try_lock

SCHEMA = ("schema",)


def stored(state: Path, **checks: dict[str, Any]) -> None:
    write_state(state, "checks/whoop.json", {"checks": checks})


def drifted(collection: str, status: str = "drift") -> dict[str, Any]:
    return {
        "kind": "collection",
        "collection": collection,
        "family": "schema_drift",
        "severity": "error",
        "status": status,
        "since": "2026-11-23T00:00:00.000Z",
        "last_logged": "2026-11-23T00:00:00.000Z",
        "details": {"new_keys": ["c"]},
        "suppressed": None,
    }


def run(capfd: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, list[dict]]:
    code = cli.main(["accept-schema", "whoop", *args])
    out = capfd.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return code, out, events


@pytest.fixture(autouse=True)
def json_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEARSTREAM_LOG_FORMAT", "json")


# @spec CHECKS-ACCEPT-001
def test_accepting_records_the_current_keys_and_marks_the_result(
    fake: Any,
    data: Path,
    state: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    fake.settings = [setting("sleep", *SCHEMA)]
    write_state(
        state, "checks/baselines/whoop/sleep.json", {"version": SIGNATURE_VERSION, "keys": ["a"]}
    )
    stored(state, whoop_sleep_schema_drift=drifted("sleep"))
    put(data, "sleep", "2026-11-24", {"a": 1, "c": 2})
    code, _out, events = run(capfd, "sleep")
    assert code == 0
    assert read_state(state, "checks/baselines/whoop/sleep.json") == {
        "version": SIGNATURE_VERSION,
        "keys": ["a", "c"],
    }
    assert read_state(state, "checks/whoop.json")["checks"]["whoop_sleep_schema_drift"]["accepted"]
    assert [e["event"] for e in events if e["event"] == "schema_baseline_set"] == [
        "schema_baseline_set"
    ]


# @spec CHECKS-ACCEPT-001
def test_the_next_checks_run_logs_the_recovery(
    fake: Any,
    data: Path,
    state: Path,
    capfd: pytest.CaptureFixture[str],
    unit_ctx: Any,
    logs: Logs,
) -> None:
    fake.settings = [setting("sleep", *SCHEMA)]
    stored(state, whoop_sleep_schema_drift=drifted("sleep"))
    put(data, "sleep", "2026-11-24", {"a": 1, "c": 2})
    assert run(capfd, "sleep")[0] == 0
    log.configure(fmt="json", level="DEBUG", write=logs.lines.append)
    run_unit(unit_ctx(), fake.settings, [])
    assert "check_recovered" in logs.names()
    row = read_state(state, "checks/whoop.json")["checks"]["whoop_sleep_schema_drift"]
    assert row["severity"] == "pass" and not row.get("accepted")


# @spec CHECKS-ACCEPT-001
def test_a_held_checks_lock_exits_5(
    fake: Any,
    data: Path,
    state: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "SECONDS_PER_MINUTE", 0.01)
    monkeypatch.setattr(accept, "EXTRA_WAIT_SECONDS", 0.05)
    fake.settings = [setting("sleep", *SCHEMA)]
    put(data, "sleep", "2026-11-24", {"a": 1})
    (state / "locks").mkdir()
    held = try_lock(state / "locks" / "whoop.checks.lock")
    try:
        code, _out, _events = run(capfd, "sleep")
    finally:
        release(held)
    assert code == 5
    assert not (state / "checks" / "baselines").exists()


# @spec CHECKS-ACCEPT-002
def test_all_accepts_every_collection_in_drift_or_with_an_unreadable_baseline(
    fake: Any,
    data: Path,
    state: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    fake.settings = [setting(c, *SCHEMA) for c in ("sleep", "cycle", "hr")]
    stored(
        state,
        whoop_sleep_schema_drift=drifted("sleep"),
        whoop_cycle_schema_drift=drifted("cycle", "baseline_unreadable"),
        whoop_hr_schema_drift={**drifted("hr"), "severity": "warning", "status": "missing"},
    )
    for collection in ("sleep", "cycle", "hr"):
        put(data, collection, "2026-11-24", {"k": 1})
    code, out, _events = run(capfd, "--all")
    assert code == 0
    assert "sleep" in out and "cycle" in out
    baselines = state / "checks" / "baselines" / "whoop"
    assert sorted(p.name for p in baselines.iterdir()) == ["cycle.json", "sleep.json"]


# @spec CHECKS-ACCEPT-003
@pytest.mark.parametrize("problem", ["not_selected", "no_payload"])
def test_nothing_to_accept_exits_2_and_changes_nothing(
    fake: Any,
    data: Path,
    state: Path,
    capfd: pytest.CaptureFixture[str],
    problem: str,
) -> None:
    fake.settings = [setting("sleep", *SCHEMA)]
    if problem == "no_payload":
        put(data, "sleep", "2026-11-24", [])
    collection = "workout" if problem == "not_selected" else "sleep"
    code, out, _events = run(capfd, collection)
    assert code == 2
    assert collection in out
    assert not (state / "checks").exists()


# @spec CHECKS-ACCEPT-004
def test_a_deselected_collections_baseline_is_kept(
    fake: Any, data: Path, state: Path, unit_ctx: Any
) -> None:
    write_state(
        state, "checks/baselines/whoop/hr.json", {"version": SIGNATURE_VERSION, "keys": ["x"]}
    )
    stored(state, whoop_hr_schema_drift=drifted("hr"))
    run_unit(unit_ctx(), [setting("sleep", "freshness", expected_empty=True)], [])
    assert read_state(state, "checks/baselines/whoop/hr.json")["keys"] == ["x"]
    assert "whoop_hr_schema_drift" not in read_state(state, "checks/whoop.json")["checks"]
