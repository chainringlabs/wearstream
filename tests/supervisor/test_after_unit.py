"""Refill and failing jobs after a unit (SUPERVISOR-AFTER-006, -FAIL, -MANUAL-004)."""

from __future__ import annotations

from pathlib import Path

from sup_helpers import (
    FakeClock,
    FakeSpawner,
    Logs,
    at,
    drive,
    seed,
    state_doc,
)

from wearstream.supervisor import Supervisor

DAY = "2026-11-23"


def _tick(supervisor: Supervisor, clock: FakeClock, when: str = "2026-11-23T10:17") -> None:
    clock.set(at(when))
    supervisor.step()


# @spec SUPERVISOR-AFTER-006
def test_days_that_do_not_succeed_need_a_refill_until_they_do(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    _tick(quiet, clock)
    drive(quiet, spawner, lambda h: 1 if h.unit.day in (DAY, "2026-11-20") else 0)
    assert sorted(state_doc(tmp_path, "backfill/whoop.range.json")["needs_refill"]) == [
        "2026-11-20",
        DAY,
    ]
    _tick(quiet, clock, "2026-11-23T11:17")
    drive(quiet, spawner, lambda h: 5 if h.unit.day == "2026-11-20" else 0)
    assert state_doc(tmp_path, "backfill/whoop.range.json")["needs_refill"] == ["2026-11-20"]


def _hour(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, hour: int, range_code: int
) -> None:
    _tick(quiet, clock, f"2026-11-23T{hour:02d}:17")
    drive(quiet, spawner, lambda h: range_code if h.unit.job == "range" else 0)


# @spec SUPERVISOR-FAIL-001, SUPERVISOR-FAIL-002, SUPERVISOR-FAIL-003
def test_a_job_failing_three_ticks_in_a_row_is_flagged_until_it_recovers(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path, logs: Logs
) -> None:
    _hour(quiet, clock, spawner, 10, 1)
    _hour(quiet, clock, spawner, 11, 1)
    assert logs.events("job_failing") == []
    assert state_doc(tmp_path, "jobs/whoop.range.json")["consecutive_failed_ticks"] == 2
    _hour(quiet, clock, spawner, 12, 5)  # only busy units: neither counts nor resets
    assert state_doc(tmp_path, "jobs/whoop.range.json")["consecutive_failed_ticks"] == 2
    _hour(quiet, clock, spawner, 13, 1)
    [failing] = logs.events("job_failing")
    assert (failing["level"], failing["job"]) == ("error", "range") and "run_failed" in failing[
        "action"
    ]
    assert state_doc(tmp_path, "jobs/whoop.range.json")["failing"] is True
    assert state_doc(tmp_path, "jobs/whoop.snapshots.json")["consecutive_failed_ticks"] == 0
    _hour(quiet, clock, spawner, 14, 1)
    assert len(logs.events("job_failing")) == 1
    _hour(quiet, clock, spawner, 15, 0)
    assert len(logs.events("job_recovered")) == 1
    doc = state_doc(tmp_path, "jobs/whoop.range.json")
    assert (doc["consecutive_failed_ticks"], doc["failing"]) == (0, False)


# @spec SUPERVISOR-FAIL-001
def test_a_crash_fails_the_tick_even_when_its_retry_succeeds(
    tmp_path: Path, make, clock: FakeClock, spawner: FakeSpawner
) -> None:
    from sup_helpers import seed_ticks

    seed_ticks(tmp_path, at("2026-11-23T09:17"))
    supervisor = make(WEARSTREAM_FAILING_TICKS="1")
    supervisor.start()
    _tick(supervisor, clock)
    drive(
        supervisor,
        spawner,
        lambda h: -9 if (h.unit.day == DAY and h.unit.trigger == "schedule") else 0,
    )
    assert state_doc(tmp_path, "jobs/whoop.range.json")["failing"] is True


# @spec SUPERVISOR-MANUAL-004
def test_manual_runs_are_not_ticks(quiet: Supervisor, clock: FakeClock, tmp_path: Path) -> None:
    before = state_doc(tmp_path, "jobs/whoop.range.json")
    seed(
        tmp_path,
        "runs/2026-11.jsonl",
        '{"type":"finish","origin":"manual","run_id":"m1","source":"whoop","job":"range","day":"2026-11-23","trigger":"manual","outcome":"failed","exit_code":1}\n',
    )
    clock.advance(120)
    quiet.step()
    assert state_doc(tmp_path, "jobs/whoop.range.json") == before


# @spec SUPERVISOR-STATE-008
def test_a_completed_tick_records_its_outcome(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    outcomes = []
    for hour, code in ((10, 0), (11, 1), (12, 5)):
        _hour(quiet, clock, spawner, hour, code)
        doc = state_doc(tmp_path, "jobs/whoop.range.json")
        outcomes.append(doc["last_tick_outcome"])
        assert doc["last_completed_tick"].startswith(f"2026-11-23T{hour:02d}:17")
    assert outcomes == ["success", "failed", "busy"]
    assert state_doc(tmp_path, "jobs/whoop.snapshots.json")["last_tick_outcome"] == "success"
