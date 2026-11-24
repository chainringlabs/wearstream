"""Credential waits and rate-limit cooldowns (SUPERVISOR-AUTH, -RATE, -STATE-007)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sup_helpers import (
    FakeClock,
    FakeSource,
    FakeSpawner,
    Logs,
    at,
    drive,
    result,
    state_doc,
)

from wearstream.supervisor import Supervisor


def _source(tmp_path: Path, name: str = "whoop") -> dict:
    return state_doc(tmp_path, f"sources/{name}.json")


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


def _source_units(spawner: FakeSpawner) -> list:
    return [h for h in spawner.handles if h.unit.job != "checks"]


# @spec SUPERVISOR-AUTH-001
def test_a_unit_needing_a_credential_puts_the_source_in_waiting(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path, logs: Logs
) -> None:
    _tick_and_end_first(
        quiet, clock, spawner, 4, result(needs_credential=True, fingerprint="fp-dead")
    )
    doc = _source(tmp_path)
    assert (doc["state"], doc["fingerprint"]) == ("waiting_for_auth", "fp-dead")
    assert quiet.queued("whoop") == [] and quiet.running("whoop") is None
    [waiting] = logs.events("source_waiting_for_auth")
    assert waiting["level"] == "error" and "wearstream auth whoop" in waiting["action"]


# @spec SUPERVISOR-AUTH-001
def test_a_reported_credential_counts_whatever_the_outcome_and_a_missing_fingerprint_is_computed(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    fakes: dict[str, FakeSource],
) -> None:
    fakes["whoop"].fp = "fp-now"
    _tick_and_end_first(quiet, clock, spawner, 75, result(needs_credential=True))
    assert (_source(tmp_path)["state"], _source(tmp_path)["fingerprint"]) == (
        "waiting_for_auth",
        "fp-now",
    )


# @spec SUPERVISOR-AUTH-001, SUPERVISOR-AUTH-002
def test_a_waiting_source_runs_nothing_until_its_fingerprint_changes(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    fakes: dict[str, FakeSource],
    logs: Logs,
) -> None:
    fakes["whoop"].fp = "fp-dead"
    _tick_and_end_first(
        quiet, clock, spawner, 4, result(needs_credential=True, fingerprint="fp-dead")
    )
    calls = fakes["whoop"].fingerprint_calls
    for _ in range(3):
        clock.advance(60)
        quiet.step()
    assert fakes["whoop"].fingerprint_calls - calls == 3
    for when in ("2026-11-23T10:40", "2026-11-23T11:40"):
        clock.set(at(when))
        drive(quiet, spawner)
    assert [h.unit.job for h in spawner.handles[1:]] == ["checks", "checks"]
    assert _source(tmp_path)["state"] == "waiting_for_auth"
    fakes["whoop"].fp = "fp-new"
    clock.advance(60)
    quiet.step()
    assert _source(tmp_path)["state"] == "active"
    assert logs.events("source_resumed")


# @spec SUPERVISOR-AUTH-003
def test_the_waiting_error_repeats_at_most_daily(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    fakes: dict[str, FakeSource],
    logs: Logs,
) -> None:
    fakes["whoop"].fp = "fp-dead"
    _tick_and_end_first(
        quiet, clock, spawner, 4, result(needs_credential=True, fingerprint="fp-dead")
    )
    for _ in range(23):
        clock.advance(3600)
        drive(quiet, spawner)
    assert len(logs.events("source_waiting_for_auth")) == 1
    clock.advance(3600 + 60)
    drive(quiet, spawner)
    assert len(logs.events("source_waiting_for_auth")) == 2


# @spec SUPERVISOR-AUTH-004, SUPERVISOR-CATCHUP-001, SUPERVISOR-STATE-007
def test_a_resumed_source_is_caught_up(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    fakes: dict[str, FakeSource],
) -> None:
    fakes["whoop"].fp = "fp-dead"
    _tick_and_end_first(
        quiet, clock, spawner, 4, result(needs_credential=True, fingerprint="fp-dead")
    )
    clock.set(at("2026-11-23T10:30"))
    fakes["whoop"].fp = "fp-new"
    quiet.step()
    queued = [(u.job, u.trigger) for u in quiet.queued("whoop")] + [
        (quiet.running("whoop").job, quiet.running("whoop").trigger)
    ]
    assert ("range", "catchup") in queued and ("snapshots", "catchup") in queued
    assert _source(tmp_path)["resumed_at"].startswith("2026-11-23T10:30")


# @spec SUPERVISOR-RATE-001, SUPERVISOR-RATE-002
def test_a_rate_limited_unit_starts_a_cooldown(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path, logs: Logs
) -> None:
    _tick_and_end_first(
        quiet, clock, spawner, 6, result(rate_limited=True, retry_after_seconds=3600)
    )
    assert quiet.queued("whoop") == [] and quiet.running("whoop") is None
    assert _source(tmp_path)["cooldown_until"].startswith("2026-11-23T11:17")
    [limited] = logs.events("source_rate_limited")
    assert limited["level"] == "warning" and limited["until"].startswith("2026-11-23T11:17")
    clock.set(at("2026-11-23T11:16:30"))
    quiet.step()
    assert len(_source_units(spawner)) == 1


# @spec SUPERVISOR-RATE-001
@pytest.mark.parametrize(
    ("retry_after", "until"), [(None, "2026-11-23T11:17"), (500_000, "2026-11-24T10:17")]
)
def test_the_cooldown_defaults_to_an_hour_and_is_capped_at_a_day(
    quiet: Supervisor,
    clock: FakeClock,
    spawner: FakeSpawner,
    tmp_path: Path,
    retry_after: int | None,
    until: str,
) -> None:
    _tick_and_end_first(
        quiet, clock, spawner, 6, result(rate_limited=True, retry_after_seconds=retry_after)
    )
    assert _source(tmp_path)["cooldown_until"].startswith(until)


# @spec SUPERVISOR-RATE-002, SUPERVISOR-RATE-004
def test_ticks_are_skipped_during_a_cooldown_and_catch_up_follows_it(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, logs: Logs
) -> None:
    _tick_and_end_first(
        quiet, clock, spawner, 6, result(rate_limited=True, retry_after_seconds=7200)
    )
    clock.set(at("2026-11-23T11:17"))
    quiet.step()
    assert {e["job"] for e in logs.events("tick_skipped")} >= {"range", "snapshots"}
    assert len(_source_units(spawner)) == 1
    clock.set(at("2026-11-23T12:17:30"))
    quiet.step()
    assert {u.trigger for u in quiet.queued("whoop")} == {"catchup"}


# @spec SUPERVISOR-RATE-003, SUPERVISOR-STATE-007
def test_three_cooldowns_without_a_success_are_flagged(
    quiet: Supervisor, clock: FakeClock, spawner: FakeSpawner, tmp_path: Path, logs: Logs
) -> None:
    limited = result(rate_limited=True, retry_after_seconds=60)
    for hour in (10, 11, 12):
        _tick_and_end_first(quiet, clock, spawner, 6, limited, f"2026-11-23T{hour}:17")
        clock.advance(120)
        quiet.step()
        drive(quiet, spawner, lambda h: (6, limited) if h.unit.job != "checks" else 0)
    [repeated] = logs.events("source_rate_limited_repeatedly")
    assert repeated["level"] == "error" and "pacing" in repeated["action"]
    assert _source(tmp_path)["repeatedly_rate_limited"] is True
    clock.set(at("2026-11-23T13:17"))
    drive(quiet, spawner)
    doc = _source(tmp_path)
    assert (doc["repeatedly_rate_limited"], doc["consecutive_rate_limits"]) == (False, 0)
    assert doc["last_source_success_at"].startswith("2026-11-23T13:17")
