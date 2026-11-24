"""Hand-started runs seen by the daemon, shutdown, and health
(SUPERVISOR-MANUAL-005/010, -LIFE-004, -HEALTH-002)."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from proc_helpers import hold_lock
from sup_helpers import (
    KILL,
    TERM,
    FakeClock,
    FakeSpawner,
    Logs,
    at,
    dirs,
    iso,
    run_log,
    seed,
    seed_ticks,
    state_doc,
)

from wearstream.cli import main
from wearstream.state import pid_namespace
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


# @spec SUPERVISOR-MANUAL-005
def test_abandoned_hand_started_runs_are_repaired_and_recorded_interrupted(
    tmp_path: Path,
    make: Callable[..., Supervisor],
    clock: FakeClock,
    spawner: FakeSpawner,
    logs: Logs,
) -> None:
    seed_ticks(tmp_path, at("2026-11-23T09:17"))
    seed(
        tmp_path,
        "inflight.json",
        {
            "units": [
                _entry(
                    "m1",
                    "range",
                    "2026-11-19",
                    "manual",
                    "2026-11-23T09:00:00.000Z",
                    origin="manual",
                    pid=999_999,
                    pidns="x",
                )
            ]
        },
    )
    leftover = _tmp(tmp_path, "sleep", "2026-11-19")
    supervisor = make()
    supervisor.start()
    supervisor.step()
    clock.advance(61)
    supervisor.step()
    assert not leftover.exists()
    assert state_doc(tmp_path, "inflight.json")["units"] == []
    assert [line["outcome"] for line in run_log(tmp_path) if line["type"] == "finish"] == [
        "interrupted"
    ]
    assert logs.events("inflight_recovered")[0]["run_id"] == "m1"
    assert not [h for h in spawner.handles if h.unit.day == "2026-11-19"]


# @spec SUPERVISOR-MANUAL-005
def test_a_live_hand_started_run_is_left_alone(
    tmp_path: Path, make: Callable[..., Supervisor], clock: FakeClock
) -> None:
    seed_ticks(tmp_path, at("2026-11-23T09:17"))
    entry = _entry(
        "m1",
        "range",
        "2026-11-19",
        "manual",
        "2026-11-23T09:00:00.000Z",
        origin="manual",
        pid=os.getpid(),
        pidns="x",
    )
    seed(tmp_path, "inflight.json", {"units": [entry]})
    fd = hold_lock(tmp_path / "state" / "locks" / "whoop.lock")
    try:
        supervisor = make()
        supervisor.start()
        for _ in range(3):
            clock.advance(61)
            supervisor.step()
    finally:
        os.close(fd)
    assert state_doc(tmp_path, "inflight.json")["units"] == [entry]


# @spec SUPERVISOR-MANUAL-010
def test_a_hand_started_runs_finish_is_reported_once(
    started: Supervisor, clock: FakeClock, tmp_path: Path, logs: Logs
) -> None:
    finish = {
        "type": "finish",
        "origin": "manual",
        "run_id": "m9",
        "source": "whoop",
        "job": "range",
        "day": "2026-11-20",
        "trigger": "manual",
        "outcome": "success",
        "exit_code": 0,
    }
    with (tmp_path / "state" / "runs" / "2026-11.jsonl").open("a") as handle:
        handle.write(json.dumps(finish) + "\n")
    for _ in range(3):
        clock.advance(61)
        started.step()
    [reported] = logs.events("manual_run_finished")
    assert (reported["level"], reported["day"], reported["outcome"]) == (
        "info",
        "2026-11-20",
        "success",
    )


# @spec SUPERVISOR-LIFE-004
def test_shutdown_stops_children_and_live_hand_started_runs(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    run_lock = hold_lock(tmp_path / "state" / "locks" / "whoop.lock")  # as the live run would
    try:
        entry = _entry(
            "m1",
            "range",
            "2026-11-19",
            "manual",
            iso(clock.now()),
            origin="manual",
            pid=sleeper.pid,
            pidns=pid_namespace(),
        )
        seed(tmp_path, "inflight.json", {"units": [entry]})
        clock.set(at("2026-11-23T10:37"))
        quiet.step()
        live = spawner.live()
        quiet.request_stop()
        assert state_doc(tmp_path, "heartbeat.json")["phase"] == "stopping"
        assert all(h.signals == [TERM] for h in live)
        assert sleeper.wait(timeout=10) != 0
        clock.advance(10)
        live[0].finish(75)
        quiet.step()
        assert not quiet.finished and len(spawner.handles) == 2
        clock.advance(36)
        quiet.step()
        assert live[1].signals == [TERM, KILL] and quiet.finished
        assert {u["run_id"] for u in state_doc(tmp_path, "inflight.json")["units"]} >= {
            h.unit.run_id for h in live
        }
    finally:
        os.close(run_lock)
        sleeper.kill()


# @spec SUPERVISOR-HEALTH-002
@pytest.mark.parametrize(
    ("heartbeat", "healthy"),
    [
        (None, False),
        ({"phase": "running", "age": 30, "pause": None}, True),
        ({"phase": "running", "age": 121, "pause": None}, False),
        ({"phase": "running", "age": 5, "pause": {"reason": "disk full"}}, False),
        ("garbage", False),
    ],
)
def test_healthcheck_reads_the_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, heartbeat: object, healthy: bool
) -> None:
    for name, value in dirs(tmp_path).items():
        monkeypatch.setenv(name, value)
    if isinstance(heartbeat, dict):
        updated = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=heartbeat["age"])
        seed(
            tmp_path,
            "heartbeat.json",
            {
                "phase": heartbeat["phase"],
                "updated_at": iso(updated),
                "pause": heartbeat["pause"],
                "sources": {"whoop": "waiting_for_auth"},
            },
        )
    elif heartbeat is not None:
        seed(tmp_path, "heartbeat.json", str(heartbeat))
    assert main(["healthcheck"]) == (0 if healthy else 1)


# @spec SUPERVISOR-HEALTH-002
def test_healthcheck_fails_on_invalid_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in dirs(tmp_path).items():
        monkeypatch.setenv(name, value)
    seed(
        tmp_path,
        "heartbeat.json",
        {"phase": "running", "updated_at": iso(dt.datetime.now(dt.UTC)), "pause": None},
    )
    monkeypatch.setenv("WEARSTREAM_LOG_FROMAT", "json")
    with pytest.raises(SystemExit) as exit_info:
        main(["healthcheck"])
    assert exit_info.value.code == 1
