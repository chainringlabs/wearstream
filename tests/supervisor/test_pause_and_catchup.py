"""Capture pause and catch-up (SUPERVISOR-PAUSE, -CATCHUP)."""

from __future__ import annotations

import datetime as dt
import errno
from collections.abc import Callable
from pathlib import Path

import pytest
from sup_helpers import (
    FakeClock,
    FakeSpawner,
    Logs,
    at,
    result,
    seed_ticks,
    state_doc,
)

from wearstream import capture
from wearstream.capture import SelfTestResult
from wearstream.supervisor import Supervisor


def _tick_and_end_first(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    code: int,
    outcome: dict | None = None,
    when: str = "2026-11-23T10:17",
) -> None:
    clock.set(at(when))
    quiet.step()
    [h for h in spawner.live() if h.unit.job != "checks"][0].finish(code, outcome)
    quiet.step()


def _pause(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    when: str = "2026-11-23T10:17",
) -> None:
    (tmp_path / "data" / "whoop" / "sleep").mkdir(parents=True, exist_ok=True)
    folder = str(tmp_path / "data" / "whoop" / "sleep")
    _tick_and_end_first(
        quiet,
        clock,
        spawner,
        3,
        result(error="disk full", environment={"errno": "ENOSPC", "folder": folder}),
        when,
    )


# @spec SUPERVISOR-PAUSE-001, SUPERVISOR-PAUSE-002
def test_an_environment_failure_pauses_every_source(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    logs: Logs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        capture,
        "self_test",
        lambda root, folder=None: SelfTestResult(False, "create", Path(root), "x"),
    )
    _pause(quiet, clock, spawner, tmp_path)
    [paused] = logs.events("capture_paused")
    assert (paused["level"], paused["errno"]) == ("error", "ENOSPC")
    assert paused["folder"].endswith("whoop/sleep") and "space" in paused["action"]
    assert state_doc(tmp_path, "heartbeat.json")["pause"]["errno"] == "ENOSPC"
    assert len(quiet.queued("whoop")) == 8 and quiet.running("whoop") is None
    clock.set(at("2026-11-23T10:40"))
    quiet.step()
    assert [h.unit.job for h in spawner.live()] == ["checks"]


# @spec SUPERVISOR-PAUSE-001
def test_a_failed_state_write_pauses_capture(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    logs: Logs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wearstream.state import StateStore

    real_write = StateStore.write

    def failing(self: StateStore, relative: str, doc: dict) -> None:
        if relative.startswith("jobs/"):
            raise OSError(errno.ENOSPC, "No space left on device")
        real_write(self, relative, doc)

    monkeypatch.setattr(StateStore, "write", failing)
    clock.set(at("2026-11-23T10:17"))
    quiet.step()
    [paused] = logs.events("capture_paused")
    assert "jobs" in paused["folder"] and paused["errno"] == "ENOSPC"


# @spec SUPERVISOR-PAUSE-003, SUPERVISOR-PAUSE-005, SUPERVISOR-CATCHUP-001
def test_capture_resumes_when_the_self_test_passes_after_backing_off(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    logs: Logs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[tuple[str, ...]] = []
    passing = {"now": False}

    def fake_self_test(root: Path, folder: Path | None = None) -> SelfTestResult:
        attempts.append((str(root), str(folder)))
        return (
            SelfTestResult(True)
            if passing["now"]
            else SelfTestResult(False, "create", Path(root), "OSError: full")
        )

    monkeypatch.setattr(capture, "self_test", fake_self_test)
    _pause(quiet, clock, spawner, tmp_path)
    for seconds, expected in ((299, 0), (2, 1), (599, 1), (2, 2), (1199, 2), (2, 3)):
        clock.advance(seconds)
        quiet.step()
        assert len(attempts) == expected
    assert attempts[0][1].endswith("whoop/sleep")
    passing["now"] = True
    clock.advance(2400)
    quiet.step()
    assert logs.events("capture_resumed")
    assert state_doc(tmp_path, "heartbeat.json")["pause"] is None
    assert any(h.unit.trigger in ("catchup", "schedule") for h in spawner.live())


# @spec SUPERVISOR-PAUSE-003
def test_the_retry_delay_is_capped_at_an_hour(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[dt.datetime] = []

    def failing(root: Path, folder: Path | None = None) -> SelfTestResult:
        attempts.append(clock.now())
        return SelfTestResult(False, "create", Path(root), "OSError: full")

    monkeypatch.setattr(capture, "self_test", failing)
    _pause(quiet, clock, spawner, tmp_path)
    for _ in range(12 * 60):
        clock.advance(60)
        quiet.step()
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(attempts, attempts[1:], strict=False)]
    assert gaps[:4] == [10, 20, 40, 60] and set(gaps[4:]) == {60}


# @spec SUPERVISOR-PAUSE-004
def test_a_relapse_within_an_hour_continues_the_backoff(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[dt.datetime] = []
    outcomes = iter([False, True, False])

    def scripted(root: Path, folder: Path | None = None) -> SelfTestResult:
        attempts.append(clock.now())
        return SelfTestResult(next(outcomes, False), "create", Path(root), "x")

    monkeypatch.setattr(capture, "self_test", scripted)
    _pause(quiet, clock, spawner, tmp_path)
    for _ in range(16):
        clock.advance(60)
        quiet.step()
    assert len(attempts) == 2  # 5 then 10 minutes: resumed at 10:32
    resumed = clock.now()
    for handle in spawner.live():
        if handle.unit.job != "checks":
            handle.finish(
                3, result(environment={"errno": "ENOSPC", "folder": str(tmp_path / "data")})
            )
            break
    quiet.step()
    for _ in range(19):
        clock.advance(60)
        quiet.step()
    assert len(attempts) == 2  # the next attempt waits 20 minutes, not 5
    clock.advance(60)
    quiet.step()
    assert len(attempts) == 3 and (attempts[2] - resumed).total_seconds() == 20 * 60


# @spec SUPERVISOR-CATCHUP-001
def test_startup_catches_up_jobs_with_no_recent_completed_tick(
    tmp_path: Path,
    make: Callable[..., Supervisor],
    clock: FakeClock,
    spawner: FakeSpawner,
    logs: Logs,
) -> None:
    seed_ticks(tmp_path, at("2026-11-23T09:30"), ("whoop.range", "whoop.snapshots"))
    seed_ticks(tmp_path, at("2026-11-23T08:37"), ("whoop.checks",))
    supervisor = make()
    supervisor.start()
    supervisor.step()
    assert [(h.unit.job, h.unit.trigger) for h in spawner.handles] == [("checks", "catchup")]
    assert {e["job"] for e in logs.events("catchup_scheduled")} == {"checks"}


# @spec SUPERVISOR-CATCHUP-001
def test_a_fresh_install_captures_at_once(
    make: Callable[..., Supervisor], spawner: FakeSpawner
) -> None:
    supervisor = make()
    supervisor.start()
    supervisor.step()
    assert {(h.unit.job, h.unit.trigger) for h in spawner.handles} == {
        ("range", "catchup"),
        ("checks", "catchup"),
    }
    assert [(u.job, u.trigger) for u in supervisor.queued("whoop")][-1] == ("snapshots", "catchup")
