"""Hand-started `wearstream run` (SUPERVISOR-MANUAL-001..003, -006..009)."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import pytest
from proc_helpers import Proc, hold_lock, wait_for
from sup_helpers import run_log, state_doc

DAY = "2026-11-20"


def _manual(
    tmp_path: Path,
    args: list[str],
    actions: list[dict] | None = None,
    job: str = "range",
    env: dict | None = None,
) -> Proc:
    return Proc(
        tmp_path, ["run", *args], {f"whoop.{job}": actions or []}, env=env, result_pipe=False
    )


def _iso(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _write_state(tmp_path: Path, relative: str, doc: dict) -> None:
    path = tmp_path / "state" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


# @spec SUPERVISOR-MANUAL-006
@pytest.mark.parametrize(
    "args",
    [["whoop", "range"], ["whoop", "snapshots", "--day", DAY], ["whoop", "checks", "--day", DAY]],
)
def test_day_is_required_exactly_for_partitioned_jobs(tmp_path: Path, args: list[str]) -> None:
    proc = _manual(tmp_path, args)
    assert proc.wait() == 2
    assert not (tmp_path / "state" / "runs").exists()


# @spec SUPERVISOR-MANUAL-001
def test_a_manual_run_records_itself_while_it_runs_and_its_finish(tmp_path: Path) -> None:
    marker = tmp_path / "running"
    proc = _manual(
        tmp_path,
        ["whoop", "range", "--day", DAY],
        [{"touch": str(marker)}, {"sleep": 1}, {"fail_request": "x"}],
    )
    wait_for(marker)
    [entry] = state_doc(tmp_path, "inflight.json")["units"]
    assert (entry["origin"], entry["source"], entry["job"], entry["day"], entry["trigger"]) == (
        "manual",
        "whoop",
        "range",
        DAY,
        "manual",
    )
    assert entry["pid"] == proc.process.pid and "pidns" in entry
    assert proc.wait() == 1
    assert state_doc(tmp_path, "inflight.json")["units"] == []
    lines = run_log(tmp_path)
    assert [(line["type"], line["origin"]) for line in lines] == [
        ("start", "manual"),
        ("finish", "manual"),
    ]
    assert (lines[1]["outcome"], lines[1]["exit_code"]) == ("failed", 1)
    assert DAY in state_doc(tmp_path, "backfill/whoop.range.json")["needs_refill"]
    assert _manual(tmp_path, ["whoop", "range", "--day", DAY]).wait() == 0
    assert DAY not in state_doc(tmp_path, "backfill/whoop.range.json")["needs_refill"]


# @spec SUPERVISOR-MANUAL-001
def test_a_manual_run_takes_the_lock_before_recording_itself(tmp_path: Path) -> None:
    fd = hold_lock(tmp_path / "state" / "locks" / "whoop.lock")
    try:
        assert _manual(tmp_path, ["whoop", "snapshots"]).wait() == 5
    finally:
        os.close(fd)
    assert run_log(tmp_path) == []
    assert (
        not (tmp_path / "state" / "inflight.json").exists()
        or state_doc(tmp_path, "inflight.json")["units"] == []
    )


# @spec SUPERVISOR-MANUAL-002
def test_a_paused_daemon_refuses_manual_runs(tmp_path: Path) -> None:
    now = dt.datetime.now(dt.UTC)
    _write_state(tmp_path, "heartbeat.json", {
        "pid": 1, "phase": "running", "updated_at": _iso(now),
        "pause": {"reason": "no space left on /data", "errno": "ENOSPC", "folder": "/data/whoop"},
    })  # fmt: skip
    proc = _manual(
        tmp_path, ["whoop", "snapshots"], [{"touch": str(tmp_path / "ran")}], job="snapshots"
    )
    assert proc.wait() == 3
    assert not (tmp_path / "ran").exists()
    [refused] = proc.named("run_refused")
    assert (
        refused["level"] == "error" and "no space left" in refused["reason"] and refused["action"]
    )


# @spec SUPERVISOR-MANUAL-002, SUPERVISOR-MANUAL-003
def test_a_stale_heartbeat_means_no_daemon_and_a_self_test(tmp_path: Path) -> None:
    old = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)
    _write_state(
        tmp_path,
        "heartbeat.json",
        {"pid": 1, "phase": "running", "updated_at": _iso(old), "pause": {"reason": "old"}},
    )
    assert _manual(tmp_path, ["whoop", "snapshots"], job="snapshots").wait() == 0


# @spec SUPERVISOR-MANUAL-003
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_without_a_daemon_a_failing_self_test_refuses_the_run(tmp_path: Path) -> None:
    from sup_helpers import dirs

    dirs(tmp_path)
    (tmp_path / "data").chmod(0o555)
    try:
        proc = _manual(
            tmp_path, ["whoop", "snapshots"], [{"touch": str(tmp_path / "ran")}], job="snapshots"
        )
        assert proc.wait() == 3
    finally:
        (tmp_path / "data").chmod(0o755)
    assert not (tmp_path / "ran").exists()
    assert proc.named("run_refused")[0]["action"]


# @spec SUPERVISOR-MANUAL-008
def test_a_source_waiting_for_a_credential_refuses_manual_runs(tmp_path: Path) -> None:
    _write_state(
        tmp_path, "sources/whoop.json", {"state": "waiting_for_auth", "fingerprint": "fp-dead"}
    )
    proc = _manual(
        tmp_path, ["whoop", "snapshots"], [{"touch": str(tmp_path / "ran")}], job="snapshots"
    )
    assert proc.wait() == 4
    assert "wearstream auth whoop" in proc.named("run_refused")[0]["action"]
    assert not (tmp_path / "ran").exists()


# @spec SUPERVISOR-MANUAL-008
def test_a_source_in_a_cooldown_refuses_manual_runs(tmp_path: Path) -> None:
    until = _iso(dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30))
    _write_state(tmp_path, "sources/whoop.json", {"state": "active", "cooldown_until": until})
    proc = _manual(tmp_path, ["whoop", "snapshots"], job="snapshots")
    assert proc.wait() == 6
    assert until in proc.named("run_refused")[0]["cooldown_until"]
    _write_state(
        tmp_path,
        "sources/whoop.json",
        {
            "state": "active",
            "cooldown_until": _iso(dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)),
        },
    )
    assert _manual(tmp_path, ["whoop", "snapshots"], job="snapshots").wait() == 0


# @spec SUPERVISOR-MANUAL-007
def test_a_manual_run_stops_itself_at_its_time_limit(tmp_path: Path) -> None:
    env = {"FAKE_SECONDS_PER_MINUTE": "1", "FAKE_GRACE_SECONDS": "2"}  # range: 5 "minutes" = 5 s
    began = time.monotonic()
    proc = _manual(tmp_path, ["whoop", "range", "--day", DAY], [{"sleep": 30}], env=env)
    assert proc.wait() == 75
    assert 4.5 < time.monotonic() - began < 15
    assert run_log(tmp_path)[-1]["outcome"] == "interrupted"


# @spec SUPERVISOR-MANUAL-007
def test_a_manual_run_still_running_after_the_grace_is_ended(tmp_path: Path) -> None:
    env = {"FAKE_SECONDS_PER_MINUTE": "1", "FAKE_GRACE_SECONDS": "2"}
    marker = tmp_path / "exchange"
    began = time.monotonic()
    proc = _manual(
        tmp_path,
        ["whoop", "range", "--day", DAY],
        [{"sleep": 3}, {"exchange": {"marker": str(marker), "seconds": 30}}],
        env=env,
    )
    assert proc.wait() != 0
    assert time.monotonic() - began < 15
    assert marker.read_text() == "in exchange"


# @spec SUPERVISOR-MANUAL-009
def test_a_manual_checks_run_uses_only_the_checks_lock(tmp_path: Path) -> None:
    source_lock = hold_lock(tmp_path / "state" / "locks" / "whoop.lock")
    _write_state(tmp_path, "sources/whoop.json", {"state": "waiting_for_auth", "fingerprint": "x"})
    try:
        proc = _manual(
            tmp_path, ["whoop", "checks"], [{"touch": str(tmp_path / "checked")}], job="checks"
        )
        assert proc.wait() == 0
    finally:
        os.close(source_lock)
    assert (tmp_path / "checked").exists()
    assert [(line["type"], line["job"], line["trigger"]) for line in run_log(tmp_path)] == [
        ("start", "checks", "manual"),
        ("finish", "checks", "manual"),
    ]
    assert not (tmp_path / "state" / "inflight.json").exists()
    checks_lock = hold_lock(tmp_path / "state" / "locks" / "whoop.checks.lock")
    try:
        assert _manual(tmp_path, ["whoop", "checks"], job="checks").wait() == 5
    finally:
        os.close(checks_lock)
