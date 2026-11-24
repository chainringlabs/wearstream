"""The `wearstream run` child a daemon starts (SUPERVISOR-CHILD)."""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest
from proc_helpers import Proc, daemon_args, hold_lock, wait_for

DAY = "2026-11-20"


def _run(
    tmp_path: Path, actions: list[dict], job: str = "range", day: str | None = DAY, **script: object
) -> Proc:
    proc = Proc(
        tmp_path,
        daemon_args("whoop", job, day if job == "range" else None),
        {f"whoop.{job}": actions, **script},
    )
    proc.wait()
    return proc


# @spec SUPERVISOR-CHILD-002
def test_a_source_unit_exits_5_while_the_source_lock_is_held(tmp_path: Path) -> None:
    fd = hold_lock(tmp_path / "state" / "locks" / "whoop.lock")
    try:
        assert _run(tmp_path, [{"touch": str(tmp_path / "ran")}]).process.returncode == 5
        checks = _run(tmp_path, [{"touch": str(tmp_path / "checked")}], job="checks")
    finally:
        os.close(fd)
    assert not (tmp_path / "ran").exists()
    assert checks.process.returncode == 0 and (tmp_path / "checked").exists()


# @spec SUPERVISOR-CHILD-002
def test_a_checks_unit_exits_5_while_the_checks_lock_is_held(tmp_path: Path) -> None:
    fd = hold_lock(tmp_path / "state" / "locks" / "whoop.checks.lock")
    try:
        assert _run(tmp_path, [], job="checks").process.returncode == 5
    finally:
        os.close(fd)


# @spec SUPERVISOR-CHILD-003
def test_the_result_reports_counts_and_what_the_source_reported(tmp_path: Path) -> None:
    proc = _run(
        tmp_path,
        [
            {"capture": {"payload": "a"}},
            {"capture": {"payload": "a"}},
            {"capture": {"payload": "b", "collection": "cycle"}},
            {"capture": {"payload": "e", "status": 500}},
            {"fail_request": "recovery: HTTP 502"},
            {"needs_auth": "fp-dead"},
            {"rate_limited": 120},
        ],
    )
    result = proc.result
    assert (
        result["written"],
        result["skipped_identical"],
        result["skipped_non_2xx"],
        result["failed"],
    ) == (2, 1, 1, 0)
    assert "recovery: HTTP 502" in result["error"]
    assert (result["needs_credential"], result["fingerprint"]) == (True, "fp-dead")
    assert (result["rate_limited"], result["retry_after_seconds"]) == (True, 120)
    assert result["environment"] is None


# @spec SUPERVISOR-CHILD-004, SUPERVISOR-CHILD-003
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_an_environment_failure_ends_the_job(tmp_path: Path) -> None:
    folder = tmp_path / "data" / "whoop" / "sleep"
    folder.mkdir(parents=True)
    folder.chmod(0o555)
    try:
        proc = _run(tmp_path, [{"capture": {"payload": "a"}}, {"touch": str(tmp_path / "after")}])
    finally:
        folder.chmod(0o755)
    assert proc.process.returncode == 3
    assert not (tmp_path / "after").exists()
    assert proc.result["environment"] == {"errno": "EACCES", "folder": str(folder / f"dt={DAY}")}


# @spec SUPERVISOR-CHILD-005
@pytest.mark.parametrize(
    ("actions", "code"),
    [
        ([{"capture": {"payload": "a"}}], 0),
        ([{"capture": {"payload": "a", "status": 404}}], 0),
        ([{"fail_request": "x"}], 1),
        ([{"rate_limited": None}, {"fail_request": "x"}], 6),
        ([{"needs_auth": "fp"}, {"rate_limited": 5}, {"fail_request": "x"}], 4),
    ],
    ids=[
        "clean",
        "non-2xx-alone",
        "failed-request",
        "rate-limit-beats-failure",
        "credential-beats-rate-limit",
    ],
)
def test_the_exit_code_is_the_first_that_applies(
    tmp_path: Path, actions: list[dict], code: int
) -> None:
    assert _run(tmp_path, actions).process.returncode == code


# @spec SUPERVISOR-CHILD-006
def test_an_unexpected_exception_exits_70(tmp_path: Path) -> None:
    proc = _run(tmp_path, [{"raise": "boom"}])
    assert proc.process.returncode == 70
    [event] = proc.named("run_exception")
    assert (event["level"], event["error_type"]) == ("warning", "RuntimeError")
    assert proc.stderr == ""


# @spec SUPERVISOR-CHILD-007, SUPERVISOR-CHILD-005
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_stop_signal_ends_the_run_with_75(tmp_path: Path, sig: int) -> None:
    marker = tmp_path / "working"
    proc = Proc(tmp_path, daemon_args("whoop", "range", DAY), {"whoop.range": [
        {"capture": {"payload": "a"}}, {"fail_request": "x"}, {"touch": str(marker)}, {"sleep": 20},
    ]})  # fmt: skip
    wait_for(marker)
    proc.process.send_signal(sig)
    assert proc.wait() == 75
    assert proc.result["written"] == 1
    assert not [p for p in (tmp_path / "data").rglob(".tmp_*")]


# @spec SUPERVISOR-CHILD-008
def test_a_stop_signal_waits_for_a_credential_exchange(tmp_path: Path) -> None:
    marker = tmp_path / "exchange"
    proc = Proc(tmp_path, daemon_args("whoop", "range", DAY), {"whoop.range": [
        {"exchange": {"marker": str(marker), "seconds": 1.5}}, {"sleep": 20},
    ]})  # fmt: skip
    wait_for(marker, "in exchange")
    proc.process.send_signal(signal.SIGTERM)
    assert proc.wait() == 75
    assert marker.read_text() == "exchange done"


# @spec SUPERVISOR-CHILD-009
def test_a_partitioned_unit_captures_into_its_own_day(tmp_path: Path) -> None:
    _run(
        tmp_path,
        [
            {"capture": {"payload": "a", "dt": "2020-01-01"}},
            {"capture": {"payload": "b", "collection": "cycle"}},
        ],
    )
    days = sorted({p.parent.name for p in (tmp_path / "data").rglob("*.meta.json")})
    assert days == [f"dt={DAY}"]


# @spec SUPERVISOR-CHILD-009
def test_a_snapshot_unit_captures_by_fetch_date(tmp_path: Path) -> None:
    _run(tmp_path, [{"capture": {"payload": "a", "collection": "profile"}}], job="snapshots")
    [sidecar] = (tmp_path / "data").rglob("*.meta.json")
    assert sidecar.parent.name.startswith("dt=") and sidecar.parent.name != f"dt={DAY}"
