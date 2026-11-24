"""Backfill passes: start, missing days, pacing, known-empty days, attempts
(SUPERVISOR-BACKFILL)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sup_helpers import FakeClock, FakeHandle, FakeSpawner, Logs, at, seed, seed_ticks, state_doc

from wearstream import capture
from wearstream.supervisor import Supervisor

# Whoop's window at 2026-11-23 is 11-16..11-23, so a pass starts at 11-15.


def _data(tmp_path: Path, day: str, collection: str = "sleep") -> None:
    (tmp_path / "data").mkdir(exist_ok=True)
    capture.capture(
        tmp_path / "data", "whoop", collection, b"{}", {"http_status": 200}, dt=day, dedupe=False
    )


def _backfilled(spawner: FakeSpawner) -> list[str]:
    return [h.unit.day for h in spawner.handles if h.unit.trigger == "backfill"]


def _run(
    supervisor: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    code: Callable[[FakeHandle], int] = lambda h: 0,
    rounds: int = 12,
) -> None:
    for _ in range(rounds):
        supervisor.step()
        for handle in spawner.live():
            handle.finish(code(handle))
        clock.advance(61)


@pytest.fixture
def start(tmp_path: Path, make: Callable[..., Supervisor]) -> Callable[..., Supervisor]:
    def build(**settings: str) -> Supervisor:
        seed_ticks(tmp_path, at("2026-11-23T09:17"))
        supervisor = make(**settings)
        supervisor.start()
        return supervisor

    return build


def _doc(tmp_path: Path) -> dict[str, Any]:
    return state_doc(tmp_path, "backfill/whoop.range.json")


# @spec SUPERVISOR-BACKFILL-001, SUPERVISOR-BACKFILL-002
def test_backfill_from_walks_newest_first_from_the_day_before_the_window(
    start: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner
) -> None:
    _run(start(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-12"), clock, spawner)
    assert _backfilled(spawner) == ["2026-11-15", "2026-11-14", "2026-11-13", "2026-11-12"]


# @spec SUPERVISOR-BACKFILL-001, SUPERVISOR-BACKFILL-003
def test_without_backfill_from_the_start_is_the_earliest_day_with_data(
    start: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    _data(tmp_path, "2026-11-13", "cycle")
    orphan = tmp_path / "data" / "whoop" / "sleep" / "dt=2026-11-05" / "sleep_1_abcdef.json"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("{}")
    _run(start(), clock, spawner)
    assert _backfilled(spawner) == ["2026-11-15", "2026-11-14"]


# @spec SUPERVISOR-BACKFILL-001
def test_with_no_data_the_start_is_the_first_run_date(
    start: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    _run(start(), clock, spawner)
    assert _backfilled(spawner) == []
    assert state_doc(tmp_path, "sources/whoop.json")["first_run"] == "2026-11-23"


# @spec SUPERVISOR-BACKFILL-003
def test_refill_days_known_empty_days_and_given_up_days(
    start: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    for day in ("2026-11-15", "2026-11-14", "2026-11-13", "2026-11-12"):
        _data(tmp_path, day)
    seed(tmp_path, "backfill/whoop.range.json", {
        "needs_refill": ["2026-11-14", "2026-11-13"],
        "known_empty": {"2026-11-11": {"recorded": "2026-11-20", "rechecked": False}},
        "attempts": {"2026-11-13": 3},
    })  # fmt: skip
    _run(start(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-10"), clock, spawner)
    assert _backfilled(spawner) == ["2026-11-14", "2026-11-10"]


# @spec SUPERVISOR-BACKFILL-004
def test_the_scan_runs_off_the_loop_and_stops_at_the_first_collection_with_data(
    tmp_path: Path,
    make: Callable[..., Supervisor],
    clock: FakeClock,
    spawner: FakeSpawner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sup_helpers import Inline, values

    _data(tmp_path, "2026-11-15", "sleep")
    asked: list[tuple[str, str]] = []
    real = capture.has_data

    def counting(root: Path, source: str, collection: str, day: str) -> bool:
        asked.append((collection, day))
        return real(root, source, collection, day)

    class Recording(Inline):
        submitted = 0

        def submit(self, fn: Any, *args: Any) -> Any:
            Recording.submitted += 1
            return super().submit(fn, *args)

    monkeypatch.setattr(capture, "has_data", counting)
    seed_ticks(tmp_path, at("2026-11-23T09:17"))
    supervisor = Supervisor(
        values(tmp_path, WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-14"),
        clock=clock,
        spawn=spawner,
        executor=Recording(),
    )
    supervisor.start()
    _run(supervisor, clock, spawner)
    assert Recording.submitted >= 1
    assert [c for c, d in asked if d == "2026-11-15"] == ["sleep"]
    assert [c for c, d in asked if d == "2026-11-14"][:4] == [
        "sleep",
        "recovery",
        "workout",
        "cycle",
    ]


# @spec SUPERVISOR-BACKFILL-005
def test_backfill_units_are_spaced_by_the_pause(
    start: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner
) -> None:
    supervisor = start(
        WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-13", WEARSTREAM_WHOOP_RANGE_BACKFILL_PAUSE="120"
    )
    supervisor.step()
    [first] = spawner.live()
    first.finish(0)
    supervisor.step()
    clock.advance(119)
    supervisor.step()
    assert spawner.live() == []
    clock.advance(2)
    supervisor.step()
    assert spawner.live()[0].unit.day == "2026-11-14"


# @spec SUPERVISOR-BACKFILL-006
def test_a_pass_starts_after_the_first_successful_scheduled_tick_of_each_day(
    start: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner, logs: Logs
) -> None:
    supervisor = start()
    _run(supervisor, clock, spawner, rounds=3)
    assert len(logs.events("backfill_pass_started")) == 1
    for when in ("2026-11-23T10:17", "2026-11-23T11:17", "2026-11-24T00:17", "2026-11-24T01:17"):
        clock.set(at(when))
        _run(supervisor, clock, spawner, rounds=12)
    assert len(logs.events("backfill_pass_started")) == 3


# @spec SUPERVISOR-BACKFILL-007
def test_a_pass_requested_during_a_pass_starts_when_it_finishes(
    start: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner, logs: Logs
) -> None:
    supervisor = start(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-01")
    supervisor.step()
    clock.set(at("2026-11-23T10:17"))
    _run(
        supervisor,
        clock,
        spawner,
        code=lambda h: 1 if h.unit.trigger == "backfill" else 0,
        rounds=40,
    )
    names = [n for n in logs.names() if n.startswith("backfill_pass")]
    assert names[:3] == ["backfill_pass_started", "backfill_pass_finished", "backfill_pass_started"]


# @spec SUPERVISOR-BACKFILL-008
def test_a_restart_resumes_the_pass_from_its_saved_position(
    tmp_path: Path,
    start: Callable[..., Supervisor],
    make: Callable[..., Supervisor],
    clock: FakeClock,
    spawner: FakeSpawner,
) -> None:
    first = start(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-10")
    first.step()
    spawner.live()[0].finish(1)
    first.step()
    assert _doc(tmp_path)["position"] == "2026-11-15" and _doc(tmp_path)["in_progress"] is True
    spawner.handles.clear()
    second = make(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-10")
    second.start()
    second.step()
    assert _backfilled(spawner)[0] == "2026-11-14"


# @spec SUPERVISOR-BACKFILL-009, SUPERVISOR-BACKFILL-013
def test_a_success_that_leaves_no_data_records_the_day_known_empty(
    start: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    def fill(handle: FakeHandle) -> int:
        if handle.unit.day == "2026-11-15":
            _data(tmp_path, "2026-11-15", "cycle")
        return 0

    _run(start(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-14"), clock, spawner, code=fill)
    doc = _doc(tmp_path)
    assert list(doc["known_empty"]) == ["2026-11-14"]
    assert doc["known_empty"]["2026-11-14"]["recorded"] == "2026-11-23"
    assert doc["counts"] == {"missing": 2, "filled": 1, "known_empty": 1, "given_up": 0}


# @spec SUPERVISOR-BACKFILL-010
def test_a_known_empty_day_is_rechecked_once_after_30_days(
    start: Callable[..., Supervisor], clock: FakeClock, spawner: FakeSpawner, tmp_path: Path
) -> None:
    _data(tmp_path, "2026-11-15")
    seed(tmp_path, "backfill/whoop.range.json", {"known_empty": {
        "2026-11-14": {"recorded": "2026-10-20", "rechecked": False},
        "2026-11-13": {"recorded": "2026-11-01", "rechecked": False},
        "2026-11-12": {"recorded": "2026-09-01", "rechecked": True},
    }})  # fmt: skip
    supervisor = start(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-12")
    _run(supervisor, clock, spawner)
    assert _backfilled(spawner) == ["2026-11-14"]
    assert _doc(tmp_path)["known_empty"]["2026-11-14"]["rechecked"] is True


# @spec SUPERVISOR-BACKFILL-011, SUPERVISOR-BACKFILL-013
@pytest.mark.parametrize(
    ("code", "counted"), [(1, True), (-9, True), (75, False), (5, False), (6, False)]
)
def test_failed_attempts_are_counted_and_the_third_abandons_the_day(
    start: Callable[..., Supervisor],
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    logs: Logs,
    code: int,
    counted: bool,
) -> None:
    seed(tmp_path, "backfill/whoop.range.json", {"attempts": {"2026-11-15": 2}})
    supervisor = start(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-15")
    supervisor.step()
    spawner.live()[0].finish(code)
    supervisor.step()
    attempts = _doc(tmp_path)["attempts"].get("2026-11-15", 0)
    assert attempts == (3 if counted else 2)
    abandoned = logs.events("backfill_day_abandoned")
    assert len(abandoned) == (1 if counted else 0)
    if counted:
        assert abandoned[0]["level"] == "warning" and abandoned[0]["day"] == "2026-11-15"
        assert _doc(tmp_path)["counts"]["given_up"] == 1


# @spec SUPERVISOR-BACKFILL-012
def test_a_later_backfill_start_ends_the_pass(
    tmp_path: Path,
    start: Callable[..., Supervisor],
    clock: FakeClock,
    spawner: FakeSpawner,
    logs: Logs,
) -> None:
    seed(
        tmp_path,
        "backfill/whoop.range.json",
        {"in_progress": True, "position": "2026-11-12", "restart_pending": False},
    )
    _run(start(WEARSTREAM_WHOOP_BACKFILL_FROM="2026-11-14"), clock, spawner, rounds=3)
    assert _backfilled(spawner) == []
    assert logs.events("backfill_pass_finished")
    assert _doc(tmp_path)["in_progress"] is False
