"""Fire times, windows, ticks and queues (SUPERVISOR-SCHED-002..006, -QUEUE)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sup_helpers import FakeClock, FakeSpawner, Logs, at, drive, seed_ticks

from wearstream.supervisor import Supervisor

RANGE_DAYS = [f"2026-11-{d}" for d in range(23, 15, -1)]


# @spec SUPERVISOR-SCHED-002, SUPERVISOR-SCHED-004
def test_fire_times_passed_before_startup_are_not_fired(
    tmp_path: Path, make: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner
) -> None:
    seed_ticks(tmp_path, at("2026-11-23T10:17"))
    clock.set(at("2026-11-23T10:20"))
    supervisor = make()
    supervisor.start()
    supervisor.step()

    def source_units() -> list:
        return [h for h in spawner.handles if h.unit.job != "checks"]

    assert spawner.handles == []
    clock.set(at("2026-11-23T11:16:59"))
    supervisor.step()
    assert source_units() == []
    clock.set(at("2026-11-23T11:17"))
    supervisor.step()
    assert {h.unit.trigger for h in source_units()} == {"schedule"}


# @spec SUPERVISOR-SCHED-003
def test_the_loop_sleeps_at_most_30_seconds(quiet: Supervisor, clock: FakeClock) -> None:
    assert quiet.sleep_seconds() == 30
    clock.set(at("2026-11-23T10:16:50"))
    assert quiet.sleep_seconds() == 10


# @spec SUPERVISOR-SCHED-004
def test_many_missed_fire_times_fire_once(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner
) -> None:
    clock.set(at("2026-11-23T13:40"))
    quiet.step()
    drive(quiet, spawner)
    jobs = [h.unit.job for h in spawner.handles]
    assert jobs.count("snapshots") == 1 and jobs.count("checks") == 1 and jobs.count("range") == 8


# @spec SUPERVISOR-SCHED-004
def test_a_fire_time_at_or_before_the_last_recorded_tick_is_not_fired(
    tmp_path: Path, make: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner
) -> None:
    seed_ticks(tmp_path, at("2026-11-23T09:37"), ("whoop.range", "whoop.snapshots"))
    seed_ticks(tmp_path, at("2026-11-23T10:37"), ("whoop.checks",))  # the clock was set back
    supervisor = make()
    supervisor.start()
    clock.set(at("2026-11-23T10:37"))
    supervisor.step()
    assert "checks" not in [h.unit.job for h in spawner.handles]
    clock.set(at("2026-11-23T11:37"))
    supervisor.step()
    assert "checks" in [h.unit.job for h in spawner.handles]


# @spec SUPERVISOR-SCHED-005
def test_a_window_is_computed_from_the_fire_times_date(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner
) -> None:
    clock.set(at("2026-11-23T23:16"))
    quiet.step()
    drive(quiet, spawner)
    spawner.handles.clear()
    clock.set(at("2026-11-24T00:00:10"))  # woke after midnight for the 23:17 fire time
    quiet.step()
    drive(quiet, spawner)
    days = [h.unit.day for h in spawner.handles if h.unit.job == "range"]
    assert days == RANGE_DAYS


# @spec SUPERVISOR-SCHED-005, SUPERVISOR-SCHED-006
def test_ticks_enqueue_their_window_newest_first_or_one_unit(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner
) -> None:
    clock.set(at("2026-11-23T10:17"))
    quiet.step()
    assert [u.day for u in quiet.queued("whoop")] == RANGE_DAYS[1:] + [None]
    assert [(u.job, u.trigger) for u in quiet.queued("whoop")][-1] == ("snapshots", "schedule")
    assert quiet.running("whoop").day == RANGE_DAYS[0]


# @spec SUPERVISOR-SCHED-005
def test_a_window_that_excludes_the_fire_date(
    tmp_path: Path, make: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner
) -> None:
    seed_ticks(
        tmp_path, at("2026-11-23T05:30"), ("garmin.daily", "garmin.reference", "garmin.checks")
    )
    supervisor = make("garmin")
    supervisor.start()
    clock.set(at("2026-11-24T05:30"))
    drive(supervisor, spawner)
    days = [h.unit.day for h in spawner.handles if h.unit.job == "daily"]
    assert days == [f"2026-11-{d:02d}" for d in range(23, 9, -1)]


# @spec SUPERVISOR-QUEUE-001, SUPERVISOR-QUEUE-002
def test_one_source_unit_and_one_checks_unit_run_at_a_time(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner
) -> None:
    clock.set(at("2026-11-23T10:37"))
    quiet.step()
    live = spawner.live()
    assert sorted(h.unit.job for h in live) == ["checks", "range"]
    live[0].finish(0) if live[0].unit.job == "checks" else live[1].finish(0)
    quiet.step()
    assert [h.unit.job for h in spawner.live()] == ["range"]
    started_days = []
    for _ in range(8):
        [running] = [h for h in spawner.live() if h.unit.job == "range"]
        started_days.append(running.unit.day)
        running.finish(0)
        quiet.step()
    assert started_days == RANGE_DAYS
    assert spawner.live()[0].unit.job == "snapshots"


# @spec SUPERVISOR-QUEUE-002
def test_checks_never_wait_for_a_slow_source(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner
) -> None:
    clock.set(at("2026-11-23T10:17"))
    quiet.step()
    clock.set(at("2026-11-23T10:37"))
    quiet.step()
    assert "checks" in [h.unit.job for h in spawner.live()]


# @spec SUPERVISOR-QUEUE-003
def test_a_tick_is_skipped_while_the_jobs_scheduled_units_remain(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, logs: Logs
) -> None:
    clock.set(at("2026-11-23T10:17"))
    quiet.step()
    clock.set(at("2026-11-23T11:17"))
    spawner.live()[0].finish(0)
    quiet.step()
    skipped = logs.events("tick_skipped")
    assert {(e["source"], e["job"]) for e in skipped} == {
        ("whoop", "range"),
        ("whoop", "snapshots"),
    }
    assert "checks" in [h.unit.job for h in spawner.handles]
    assert sum(1 for u in quiet.queued("whoop") if u.job == "range") == 6


# @spec SUPERVISOR-QUEUE-004
def test_retries_and_backfill_never_block_a_tick(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, logs: Logs
) -> None:
    clock.set(at("2026-11-23T10:17"))
    quiet.step()
    for _ in range(8):
        spawner.live()[0].finish(0)
        quiet.step()
    spawner.live()[0].finish(-9)  # snapshots crashes and is retried
    quiet.step()
    [retry] = spawner.live()
    assert (retry.unit.job, retry.unit.trigger) == ("snapshots", "retry")
    clock.set(at("2026-11-23T11:17"))
    quiet.step()
    assert logs.events("tick_skipped") == []
    assert [u.trigger for u in quiet.queued("whoop")][-1] == "schedule"


# @spec SUPERVISOR-QUEUE-005
def test_backfill_units_wait_for_an_empty_queue(
    tmp_path: Path, make: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner
) -> None:
    seed_ticks(tmp_path, at("2026-11-23T09:17"))
    supervisor = make(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-14")
    supervisor.start()
    clock.set(at("2026-11-23T10:17"))
    supervisor.step()
    order = []
    for _ in range(12):
        live = [h for h in spawner.live() if h.unit.job != "checks"]
        if not live:
            break
        order.append(live[0].unit.trigger)
        assert len(supervisor.queued("whoop")) == 0 or live[0].unit.trigger != "backfill"
        live[0].finish(0)
        supervisor.step()
        clock.advance(61)
        supervisor.step()
    assert order[:9] == ["schedule"] * 9
    assert "backfill" in order[9:]
