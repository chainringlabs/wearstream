"""`wearstream status` (OPERATOR-STATUS-001..005)."""

from __future__ import annotations

import datetime as dt
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from wearstream import cli
from wearstream.state import iso

NOW = dt.datetime.now(dt.UTC)


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("data", "state", "secrets"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"WEARSTREAM_{name.upper()}_DIR", str(tmp_path / name))
    return tmp_path / "state"


def put(state: Path, relative: str, doc: Any) -> None:
    path = state / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))


def ago(**delta: float) -> str:
    return iso(NOW - dt.timedelta(**delta))


def healthy(state: Path, **sources: str) -> None:
    put(
        state,
        "heartbeat.json",
        {
            "pid": 7,
            "version": "1.2.3",
            "phase": "running",
            "updated_at": ago(seconds=5),
            "pause": None,
            "sources": sources or {"whoop": "active"},
        },
    )
    put(state, "sources/whoop.json", {"state": "active", "last_source_success_at": ago(hours=1)})
    put(
        state,
        "jobs/whoop.range.json",
        {
            "last_tick": ago(hours=1),
            "last_completed_tick": ago(hours=1),
            "last_tick_outcome": "success",
            "consecutive_failed_ticks": 0,
            "failing": False,
        },
    )


def status(capfd: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli.main(["status", *args])
    return code, capfd.readouterr().out


def as_json(capfd: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code, out = status(capfd, "--json")
    return code, json.loads(out)


def kinds(report: dict[str, Any]) -> list[str]:
    return [item["kind"] for item in report["needs_attention"]]


# @spec OPERATOR-STATUS-004
def test_a_state_folder_that_never_held_state_exits_3(
    state: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code, out = status(capfd)
    assert code == 3
    assert "never" in out


# @spec OPERATOR-STATUS-004, OPERATOR-STATUS-005
def test_nothing_needing_attention_exits_0(state: Path, capfd: pytest.CaptureFixture[str]) -> None:
    healthy(state)
    code, report = as_json(capfd)
    assert code == 0
    assert report["schema_version"] == 1
    assert report["needs_attention"] == []
    assert report["daemon"]["running"] is True
    assert (report["daemon"]["phase"], report["daemon"]["version"]) == ("running", "1.2.3")


# @spec OPERATOR-STATUS-002, OPERATOR-STATUS-003
@pytest.mark.parametrize("beat", ["missing", "stale", "unreadable"])
def test_a_daemon_that_is_not_running_needs_attention(
    state: Path, capfd: pytest.CaptureFixture[str], beat: str
) -> None:
    healthy(state)
    if beat == "missing":
        (state / "heartbeat.json").unlink()
    elif beat == "stale":
        doc = json.loads((state / "heartbeat.json").read_text())
        put(state, "heartbeat.json", {**doc, "updated_at": ago(seconds=121)})
    else:
        put(state, "heartbeat.json", "{")
    code, report = as_json(capfd)
    assert code == 1
    assert report["daemon"]["running"] is False
    assert "daemon_not_running" in kinds(report)
    assert all(item["action"] for item in report["needs_attention"])


# @spec OPERATOR-STATUS-003
def test_each_condition_needing_a_person_is_listed_with_an_action(
    state: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    healthy(state, whoop="waiting_for_auth", garmin="active")
    beat = json.loads((state / "heartbeat.json").read_text())
    put(state, "heartbeat.json", {**beat, "pause": {"reason": "disk full", "errno": "ENOSPC"}})
    put(state, "sources/whoop.json", {"state": "waiting_for_auth", "since": ago(hours=3)})
    put(state, "sources/garmin.json", {"state": "active", "repeatedly_rate_limited": True})
    put(state, "jobs/garmin.daily.json", {"failing": True, "consecutive_failed_ticks": 3})
    put(state, "backfill/garmin.daily.json", "not json")
    error = {"severity": "error", "status": "stale", "since": ago(hours=5), "suppressed": None}
    put(
        state,
        "checks/garmin.json",
        {
            "checks": {
                "garmin_sleep_freshness": error,
                "garmin_hrv_freshness": {**error, "suppressed": "rate_limited"},
                "garmin_hr_schema_drift": {**error, "status": "drift", "accepted": True},
                "garmin_rhr_completeness": {**error, "severity": "warning", "status": "gaps"},
            }
        },
    )
    code, report = as_json(capfd)
    assert code == 1
    assert sorted(kinds(report)) == [
        "capture_paused",
        "check_failing",
        "job_failing",
        "repeatedly_rate_limited",
        "unreadable_document",
        "waiting_for_auth",
    ]
    assert all(item["action"] for item in report["needs_attention"])
    failing = [i for i in report["needs_attention"] if i["kind"] == "check_failing"]
    assert [i["check"] for i in failing] == ["garmin_sleep_freshness"]


# @spec OPERATOR-STATUS-003
def test_only_the_sources_the_heartbeat_lists_are_considered(
    state: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    healthy(state)
    put(state, "sources/garmin.json", {"state": "waiting_for_auth"})
    put(state, "jobs/garmin.daily.json", {"failing": True})
    code, report = as_json(capfd)
    assert (code, report["needs_attention"]) == (0, [])
    assert list(report["sources"]) == ["whoop"]


# @spec OPERATOR-STATUS-002
def test_the_report_shows_sources_jobs_units_backfill_and_checks(
    state: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    healthy(state)
    put(
        state,
        "inflight.json",
        {"units": [{"run_id": "r9", "source": "whoop", "job": "range", "day": "2026-11-23"}]},
    )
    put(
        state,
        "backfill/whoop.range.json",
        {
            "in_progress": True,
            "position": "2026-10-01",
            "counts": {"missing": 4, "filled": 2, "known_empty": 1, "given_up": 0},
        },
    )
    put(
        state,
        "checks/whoop.json",
        {
            "checks": {
                "whoop_sleep_completeness": {
                    "severity": "warning",
                    "status": "gaps",
                    "since": ago(hours=2),
                    "suppressed": None,
                }
            }
        },
    )
    code, report = as_json(capfd)
    whoop = report["sources"]["whoop"]
    assert whoop["state"] == "active"
    assert whoop["jobs"]["range"]["last_tick_outcome"] == "success"
    assert whoop["in_flight"][0]["run_id"] == "r9"
    assert whoop["backfill"]["range"]["position"] == "2026-10-01"
    assert whoop["backfill"]["range"]["counts"]["missing"] == 4
    assert report["checks"][0]["check"] == "whoop_sleep_completeness"
    assert code == 0


# @spec OPERATOR-STATUS-002
def test_the_text_report_is_in_order(state: Path, capfd: pytest.CaptureFixture[str]) -> None:
    healthy(state)
    put(state, "jobs/whoop.range.json", {"failing": True, "last_tick_outcome": "failed"})
    put(
        state,
        "checks/whoop.json",
        {
            "checks": {
                "whoop_sleep_freshness": {
                    "severity": "error",
                    "status": "stale",
                    "since": ago(hours=2),
                    "suppressed": None,
                }
            }
        },
    )
    code, out = status(capfd)
    assert code == 1
    order = [out.index(heading) for heading in ("Daemon", "Needs attention", "whoop", "Checks")]
    assert order == sorted(order)
    assert "whoop_sleep_freshness" in out and "range" in out and "failed" in out


# @spec OPERATOR-STATUS-001
def test_status_reads_only_state(
    state: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    healthy(state)

    def no_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("status must not use the network")

    monkeypatch.setattr(socket, "socket", no_network)
    data = state.parent / "data"
    data.chmod(0)
    try:
        code, _report = as_json(capfd)
    finally:
        data.chmod(0o755)
    assert code == 0


# @spec OPERATOR-STATUS-002
def test_the_text_report_shows_waits_pauses_units_and_backfill(
    state: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    healthy(state)
    put(state, "heartbeat.json", {"updated_at": ago(seconds=300), "sources": ["whoop"]})
    put(state, "sources/whoop.json", {"state": "waiting_for_auth", "since": ago(hours=2)})
    put(
        state, "inflight.json", {"units": [{"run_id": "r1", "source": "whoop", "job": "snapshots"}]}
    )
    put(
        state,
        "backfill/whoop.range.json",
        {"in_progress": False, "counts": {"filled": 3}, "needs_refill": ["2026-11-01"]},
    )
    code, out = status(capfd)
    assert code == 1
    assert "Daemon: not running (stale heartbeat)" in out
    assert "waiting for a credential since" in out
    assert "in flight: snapshots (run r1)" in out
    assert "no pass running" in out and "filled 3" in out and "1 days need a refill" in out
    assert "Checks: none warning, failing or suppressed" in out
    put(state, "heartbeat.json", {"updated_at": ago(seconds=1), "pause": {"reason": "disk full"}})
    put(state, "sources/whoop.json", {"cooldown_until": ago(hours=-1)})
    out = status(capfd)[1]
    assert "capture paused: disk full" in out and "rate-limited until" in out
