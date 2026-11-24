"""Repair and retry after a unit (SUPERVISOR-AFTER-001..005)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from proc_helpers import hold_lock
from sup_helpers import FakeClock, FakeSpawner, Logs, at, result, run_log, state_doc

from wearstream.supervisor import Supervisor

DAY = "2026-11-23"


def _tick(supervisor: Supervisor, clock: FakeClock, when: str = "2026-11-23T10:17") -> None:
    clock.set(at(when))
    supervisor.step()


def _tmp(tmp_path: Path, collection: str, day: str) -> Path:
    path = tmp_path / "data" / "whoop" / collection / f"dt={day}" / ".tmp_leftover"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return path


# @spec SUPERVISOR-AFTER-001
def test_a_crashed_partitioned_unit_is_repaired_before_the_next_unit(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path, logs: Logs
) -> None:
    leftover, other_day = _tmp(tmp_path, "sleep", DAY), _tmp(tmp_path, "sleep", "2026-11-22")
    _tick(quiet, clock)
    spawner.live()[0].finish(-9)
    quiet.step()
    assert not leftover.exists() and other_day.exists()
    assert [line["type"] for line in run_log(tmp_path)][:3] == ["start", "finish", "repaired"]
    assert logs.events("run_repaired")[0]["day"] == DAY


# @spec SUPERVISOR-AFTER-001
def test_a_snapshot_unit_is_repaired_over_every_date_it_ran(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    before, first, second = (
        _tmp(tmp_path, "profile", d) for d in ("2026-11-22", "2026-11-23", "2026-11-24")
    )
    _tick(quiet, clock, "2026-11-23T23:17")
    for _ in range(20):
        snapshots = [h for h in spawner.live() if h.unit.job == "snapshots"]
        if snapshots:
            break
        for handle in spawner.live():
            handle.finish(0)
        quiet.step()
    [snapshot] = snapshots
    clock.set(at("2026-11-24T00:01"))
    snapshot.finish(70)
    quiet.step()
    assert before.exists() and not first.exists() and not second.exists()


# @spec SUPERVISOR-AFTER-002
def test_repair_waits_for_the_source_lock_and_holds_the_queue(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    leftover = _tmp(tmp_path, "sleep", DAY)
    _tick(quiet, clock)
    fd = hold_lock(tmp_path / "state" / "locks" / "whoop.lock")
    try:
        spawner.live()[0].finish(-9)
        quiet.step()
        clock.advance(31)
        quiet.step()
        assert leftover.exists() and spawner.live() == []
    finally:
        os.close(fd)
    clock.advance(29)
    quiet.step()
    assert leftover.exists()
    clock.advance(2)
    quiet.step()
    assert not leftover.exists()
    assert spawner.live()[0].unit.day == "2026-11-22"


# @spec SUPERVISOR-AFTER-003
def test_checks_units_are_never_repaired_retried_or_allowed_to_change_a_source(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    _tick(quiet, clock, "2026-11-23T10:37")
    [checks] = [h for h in spawner.live() if h.unit.job == "checks"]
    checks.finish(-9, result(needs_credential=True, fingerprint="x", rate_limited=True))
    quiet.step()
    assert "repaired" not in [line["type"] for line in run_log(tmp_path) if line["job"] == "checks"]
    assert quiet.queued_checks("whoop") == [] and quiet.running_checks("whoop") is None
    assert (
        not (tmp_path / "state" / "sources" / "whoop.json").exists()
        or state_doc(tmp_path, "sources/whoop.json")["state"] == "active"
    )


def _first_range_unit_ends(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, code: int
) -> list[tuple[str, str | None, str]]:
    _tick(quiet, clock)
    spawner.live()[0].finish(code)
    quiet.step()
    return [(u.job, u.day, u.trigger) for u in quiet.queued("whoop")]


# @spec SUPERVISOR-AFTER-004
@pytest.mark.parametrize("code", [-9, 70, 75])
def test_crashed_or_interrupted_scheduled_units_are_retried_once_at_the_back(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, code: int
) -> None:
    queued = _first_range_unit_ends(quiet, clock, spawner, code)
    assert queued[-1] == ("range", DAY, "retry")
    assert [q for q in queued if q[2] == "retry"] == [("range", DAY, "retry")]


# @spec SUPERVISOR-AFTER-004
def test_a_killed_scheduled_unit_is_retried(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner
) -> None:
    _tick(quiet, clock)
    clock.advance(301)
    quiet.step()
    spawner.live()[0].finish(-15)
    quiet.step()
    assert quiet.queued("whoop")[-1].trigger == "retry"


# @spec SUPERVISOR-AFTER-005
@pytest.mark.parametrize("code", [1, 5])
def test_failed_or_busy_units_are_not_retried(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, code: int
) -> None:
    assert "retry" not in [q[2] for q in _first_range_unit_ends(quiet, clock, spawner, code)]


# @spec SUPERVISOR-AFTER-004, SUPERVISOR-AFTER-005
@pytest.mark.parametrize(("code", "again"), [(-9, False), (70, False), (75, True)])
def test_a_retry_is_retried_only_when_interrupted(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, code: int, again: bool
) -> None:
    _first_range_unit_ends(quiet, clock, spawner, -9)
    for _ in range(8):  # the rest of the window, then snapshots
        spawner.live()[0].finish(0)
        quiet.step()
    [retry] = spawner.live()
    assert retry.unit.trigger == "retry"
    retry.finish(code)
    quiet.step()
    assert (
        [u.trigger for u in quiet.queued("whoop")] + [h.unit.trigger for h in spawner.live()]
        == ["retry"]
    ) is again
