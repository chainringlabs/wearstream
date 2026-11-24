"""What the daemon logs (SUPERVISOR-LOG, OPERATOR-LOG-005, -LOG-009)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sup_helpers import FakeClock, FakeHandle, FakeSpawner, Logs, at, drive, result

from wearstream import __version__
from wearstream.supervisor import Supervisor


# @spec OPERATOR-LOG-005
def test_the_daemon_announces_itself_with_its_schedules(
    make: Callable[..., Supervisor], logs: Logs
) -> None:
    make("whoop,garmin").start()
    [started] = logs.events("daemon_started")
    assert started["version"] == __version__ and started["sources"] == ["whoop", "garmin"]
    assert started["schedules"]["whoop.range"] == "17 * * * *"
    assert started["schedules"]["garmin.checks"] == "7 3-21/6 * * *"


# @spec OPERATOR-LOG-009
def test_garmin_library_debug_warns_at_every_start(
    make: Callable[..., Supervisor], logs: Logs
) -> None:
    make("garmin", WEARSTREAM_GARMIN_LIBRARY_DEBUG="true").start()
    assert logs.events("library_debug_enabled")[0]["level"] == "warning"


# @spec SUPERVISOR-LOG-001
def test_run_events_carry_the_unit(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, logs: Logs
) -> None:
    clock.set(at("2026-11-23T10:17"))
    drive(
        quiet,
        spawner,
        lambda h: 1 if h.unit.day == "2026-11-20" else (70 if h.unit.job == "snapshots" else 0),
    )
    for name, level in (
        ("run_started", "info"),
        ("run_finished", "info"),
        ("run_failed", "warning"),
        ("run_crashed", "warning"),
    ):
        events = logs.events(name)
        assert events, name
        for event in events:
            assert event["level"] == level
            for key in ("source", "job", "run_id", "trigger"):
                assert key in event, (name, key)
    finished = logs.events("run_finished")[0]
    assert finished["day"] == "2026-11-23" and "duration_s" in finished and finished["written"] == 1
    assert logs.events("run_failed")[0]["day"] == "2026-11-20"


# @spec SUPERVISOR-LOG-002
def test_every_error_says_what_to_do(
    make: Callable[..., Supervisor],
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    logs: Logs,
    fakes: dict,
) -> None:
    from sup_helpers import seed_ticks

    seed_ticks(
        tmp_path, at("2026-11-23T09:17"), ("garmin.daily", "garmin.reference", "garmin.checks")
    )
    seed_ticks(tmp_path, at("2026-11-23T09:17"))
    supervisor = make("whoop,garmin")
    supervisor.start()
    supervisor.step()
    fakes["whoop"].fp = "fp-dead"
    clock.set(at("2026-11-23T10:17"))
    supervisor.step()
    clock.advance(301)
    supervisor.step()

    def whoop() -> FakeHandle:
        return [h for h in spawner.live() if h.unit.source == "whoop" and h.unit.job != "checks"][0]

    whoop().finish(-15)
    supervisor.step()
    whoop().finish(4, result(needs_credential=True, fingerprint="fp-dead"))
    supervisor.step()
    clock.set(at("2026-11-24T05:30"))
    supervisor.step()
    garmin = [h for h in spawner.live() if h.unit.source == "garmin" and h.unit.job == "daily"][0]
    garmin.finish(3, result(environment={"errno": "EROFS", "folder": str(tmp_path / "data")}))
    supervisor.step()
    errors = [e for e in logs.events() if e["level"] == "error"]
    assert {e["event"] for e in errors} >= {
        "run_killed",
        "source_waiting_for_auth",
        "capture_paused",
    }
    assert all(e.get("action") for e in errors)
