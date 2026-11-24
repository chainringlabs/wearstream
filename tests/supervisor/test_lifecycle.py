"""Startup, recovery and in-flight records
(SUPERVISOR-LIFE-002/003, -HEALTH-001, -STATE-003/006)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from sup_helpers import (
    FakeClock,
    FakeSpawner,
    Logs,
    at,
    run_log,
    seed,
    seed_ticks,
    state_doc,
)

from wearstream.supervisor import Supervisor


def _entry(
    run_id: str,
    job: str,
    day: str | None,
    trigger: str,
    started: str,
    origin: str = "daemon",
    **extra: object,
) -> dict:
    return {
        "run_id": run_id,
        "source": "whoop",
        "job": job,
        "day": day,
        "trigger": trigger,
        "started_at": started,
        "origin": origin,
        **extra,
    }


def _tmp(tmp_path: Path, collection: str, day: str) -> Path:
    path = tmp_path / "data" / "whoop" / collection / f"dt={day}" / ".tmp_cut"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return path


# @spec SUPERVISOR-LIFE-002, SUPERVISOR-HEALTH-001
def test_startup_writes_the_heartbeat_and_enters_running(
    started: Supervisor, tmp_path: Path
) -> None:
    beat = state_doc(tmp_path, "heartbeat.json")
    assert (beat["phase"], beat["pid"], beat["pause"]) == ("running", os.getpid(), None)
    assert beat["sources"] == {"whoop": "active"} and beat["version"]
    assert beat["updated_at"].startswith("2026-11-23T10:00")


# @spec SUPERVISOR-LIFE-002
def test_a_failing_self_test_starts_with_capture_paused(
    tmp_path: Path,
    make: Callable[..., Supervisor],
    spawner: FakeSpawner,
    monkeypatch: pytest.MonkeyPatch,
    logs: Logs,
) -> None:
    from wearstream import capture
    from wearstream.capture import SelfTestResult

    monkeypatch.setattr(
        capture,
        "self_test",
        lambda root, folder=None: SelfTestResult(
            False, "link", Path(root), "OSError: not supported"
        ),
    )
    supervisor = make()
    supervisor.start()
    supervisor.step()
    assert state_doc(tmp_path, "heartbeat.json")["pause"] is not None
    assert [h.unit.job for h in spawner.handles] == ["checks"]
    assert "hard" in logs.events("capture_paused")[0]["action"]


# @spec SUPERVISOR-LIFE-003, SUPERVISOR-AFTER-001
def test_daemon_units_left_in_flight_are_recovered_before_anything_else(
    tmp_path: Path, make: Callable[..., Supervisor], spawner: FakeSpawner, logs: Logs
) -> None:
    seed_ticks(tmp_path, at("2026-11-23T09:17"))
    seed(
        tmp_path,
        "heartbeat.json",
        {"pid": 1, "phase": "stopping", "updated_at": "2026-11-23T00:10:00.000Z"},
    )
    seed(tmp_path, "inflight.json", {"units": [
        _entry("a", "range", "2026-11-22", "schedule", "2026-11-23T00:05:00.000Z"),
        _entry("b", "snapshots", None, "catchup", "2026-11-22T23:50:00.000Z"),
        _entry("c", "range", "2026-11-10", "backfill", "2026-11-23T00:06:00.000Z"),
        _entry("d", "range", "2026-11-21", "schedule", "2026-11-23T00:01:00.000Z",
               signalled="limit"),
        _entry("m", "range", "2026-11-19", "manual", "2026-11-23T00:02:00.000Z",
               origin="manual", pid=999_999, pidns="x"),
    ]})  # fmt: skip
    leftovers = [
        _tmp(tmp_path, "sleep", "2026-11-22"),
        _tmp(tmp_path, "profile", "2026-11-22"),
        _tmp(tmp_path, "profile", "2026-11-23"),
    ]
    untouched = _tmp(tmp_path, "profile", "2026-11-20")
    supervisor = make()
    supervisor.start()
    supervisor.step()
    finishes = {
        line["run_id"]: line["outcome"] for line in run_log(tmp_path) if line["type"] == "finish"
    }
    assert finishes == {"a": "interrupted", "b": "interrupted", "c": "interrupted", "d": "killed"}
    assert not any(p.exists() for p in leftovers) and untouched.exists()
    retried = [(h.unit.job, h.unit.day) for h in spawner.handles if h.unit.trigger == "retry"]
    retried += [(u.job, u.day) for u in supervisor.queued("whoop") if u.trigger == "retry"]
    assert sorted(retried, key=str) == sorted(
        [("range", "2026-11-22"), ("snapshots", None), ("range", "2026-11-21")], key=str
    )
    assert len(logs.events("inflight_recovered")) == 4
    assert [u["run_id"] for u in state_doc(tmp_path, "inflight.json")["units"]] == ["m"] or "m" in [
        u["run_id"] for u in state_doc(tmp_path, "inflight.json")["units"]
    ]


# @spec SUPERVISOR-STATE-006
def test_an_unparseable_inflight_falls_back_to_the_run_log(
    tmp_path: Path, make: Callable[..., Supervisor], logs: Logs
) -> None:
    seed_ticks(tmp_path, at("2026-11-23T09:17"))
    seed(
        tmp_path,
        "heartbeat.json",
        {"pid": 1, "phase": "running", "updated_at": "2026-11-23T00:10:00.000Z"},
    )
    seed(tmp_path, "inflight.json", "{garbage")
    line = {
        "type": "start",
        "origin": "daemon",
        "source": "whoop",
        "job": "range",
        "trigger": "schedule",
    }
    seed(
        tmp_path,
        "runs/2026-10.jsonl",
        json.dumps({**line, "run_id": "old", "day": "2026-10-30", "at": "2026-10-31T23:59:00.000Z"})
        + "\n",
    )
    seed(tmp_path, "runs/2026-11.jsonl", "\n".join(json.dumps(x) for x in [
        {**line, "run_id": "done", "day": "2026-11-22", "at": "2026-11-23T00:01:00.000Z"},
        {"type": "finish", "run_id": "done", "origin": "daemon", "outcome": "success"},
        {**line, "run_id": "cut", "day": "2026-11-23", "at": "2026-11-23T00:05:00.000Z"},
    ]) + "\n")  # fmt: skip
    supervisor = make()
    supervisor.start()
    supervisor.step()
    recovered = {e["run_id"] for e in logs.events("inflight_recovered")}
    assert recovered == {"old", "cut"}


# @spec SUPERVISOR-STATE-003
def test_a_unit_is_recorded_in_flight_before_its_child_starts_and_removed_after(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    seen: list[list[str]] = []
    real = spawner.__call__

    def checking(unit, job):  # type: ignore[no-untyped-def]
        seen.append([u["run_id"] for u in state_doc(tmp_path, "inflight.json")["units"]])
        return real(unit, job)

    quiet._spawn = checking  # noqa: SLF001 - the supervisor's spawn hook
    clock.set(at("2026-11-23T10:17"))
    quiet.step()
    [handle] = spawner.live()
    assert handle.unit.run_id in seen[0]
    handle.finish(0)
    quiet.step()
    assert handle.unit.run_id not in [
        u["run_id"] for u in state_doc(tmp_path, "inflight.json")["units"]
    ]
