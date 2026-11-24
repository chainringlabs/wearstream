"""Time limits and outcomes (SUPERVISOR-LIMIT, -OUTCOME-002/003, -LOG-003)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sup_helpers import (
    KILL,
    TERM,
    FakeClock,
    FakeSpawner,
    Logs,
    at,
    result,
    run_log,
)

from wearstream.supervisor import Supervisor

DAY = "2026-11-23"


def _tick(supervisor: Supervisor, clock: FakeClock, when: str = "2026-11-23T10:17") -> None:
    clock.set(at(when))
    supervisor.step()


def _finishes(tmp_path: Path) -> list[dict]:
    return [line for line in run_log(tmp_path) if line["type"] == "finish"]


# @spec SUPERVISOR-LIMIT-001, SUPERVISOR-LIMIT-002
def test_a_unit_over_its_time_limit_is_stopped_then_killed(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner
) -> None:
    _tick(quiet, clock)
    [unit] = spawner.live()
    clock.wall += dt.timedelta(hours=3)  # wall-clock jumps do not count
    clock.mono += 299
    quiet.step()
    assert unit.signals == []
    clock.mono += 2
    quiet.step()
    assert unit.signals == [TERM]
    clock.mono += 44
    quiet.step()
    assert unit.signals == [TERM]
    clock.mono += 2
    quiet.step()
    assert unit.signals == [TERM, KILL]


# @spec SUPERVISOR-OUTCOME-002, SUPERVISOR-LOG-003
def test_a_unit_signalled_for_its_limit_is_killed_whatever_its_exit(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path, logs: Logs
) -> None:
    _tick(quiet, clock)
    [unit] = spawner.live()
    clock.advance(301)
    quiet.step()
    unit.finish(0)
    quiet.step()
    assert _finishes(tmp_path)[0]["outcome"] == "killed"
    [killed] = logs.events("run_killed")
    assert killed["level"] == "error" and "time limit" in killed["action"]


# @spec SUPERVISOR-LOG-003
def test_a_killed_checks_unit_points_at_a_hanging_check(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, logs: Logs
) -> None:
    _tick(quiet, clock, "2026-11-23T10:37")
    [checks] = [h for h in spawner.live() if h.unit.job == "checks"]
    clock.advance(601)
    quiet.step()
    checks.finish(75)
    quiet.step()
    [killed] = logs.events("run_killed")
    assert "check" in killed["action"] and "hang" in killed["action"]


# @spec SUPERVISOR-OUTCOME-002
@pytest.mark.parametrize(
    ("code", "outcome"),
    [(0, "success"), (1, "failed"), (3, "environment"), (4, "needs_auth"), (5, "busy"),
     (6, "rate_limited"), (75, "interrupted"), (70, "crashed"), (2, "crashed"), (99, "crashed"),
     (-9, "crashed"), (-15, "crashed")],
)  # fmt: skip
def test_outcomes_follow_the_exit_code(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    code: int,
    outcome: str,
) -> None:
    _tick(quiet, clock)
    spawner.live()[0].finish(
        code,
        result(environment={"errno": "ENOSPC", "folder": str(tmp_path / "data")})
        if code == 3
        else None,
    )
    quiet.step()
    finish = _finishes(tmp_path)[0]
    assert (finish["outcome"], finish["exit_code"]) == (outcome, code)


# @spec SUPERVISOR-OUTCOME-002
def test_a_usage_exit_is_logged_with_its_reason(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, logs: Logs
) -> None:
    _tick(quiet, clock)
    spawner.live()[0].finish(2)
    quiet.step()
    assert logs.events("run_crashed")[0]["reason"] == "usage"


# @spec SUPERVISOR-OUTCOME-003
@pytest.mark.parametrize("bad", [None, {"written": "three"}, {"unexpected": True}])
def test_a_missing_or_invalid_result_leaves_counts_unknown(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path, bad: object
) -> None:
    _tick(quiet, clock)
    spawner.live()[0].finish(0, bad)  # type: ignore[arg-type]
    quiet.step()
    finish = _finishes(tmp_path)[0]
    assert (finish["outcome"], finish["counts"]) == ("success", None)
