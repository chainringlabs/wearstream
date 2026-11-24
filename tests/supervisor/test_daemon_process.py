"""The real daemon process (SUPERVISOR-LIFE-001, -LIFE-002, -LIFE-004)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from proc_helpers import coverage_env
from sup_helpers import (
    dirs,
    state_doc,
)


def _daemon(
    tmp_path: Path, env: dict[str, str] | None = None, script: dict | None = None
) -> subprocess.Popen:
    from proc_helpers import LAUNCHER

    if script is not None or not (tmp_path / "script.json").exists():
        (tmp_path / "script.json").write_text(json.dumps(script or {}))
    full = {
        "PATH": os.environ.get("PATH", ""),
        **dirs(tmp_path),
        "WEARSTREAM_SOURCES": "whoop",
        "WEARSTREAM_LOG_FORMAT": "json",
        "FAKE_SOURCE_SCRIPT": str(tmp_path / "script.json"),
        "FAKE_GRACE_SECONDS": "2",
        **coverage_env(),
        **(env or {}),
    }
    return subprocess.Popen(
        [sys.executable, LAUNCHER, "daemon"],
        env=full,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _events(output: bytes) -> list[dict]:
    return [json.loads(line) for line in output.decode().splitlines() if line.startswith("{")]


def _wait_running(tmp_path: Path, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if state_doc(tmp_path, "heartbeat.json")["phase"] == "running":
                return
        except OSError, ValueError, KeyError:
            pass
        time.sleep(0.05)
    raise AssertionError("the daemon never reached running")


# @spec SUPERVISOR-LIFE-001, SUPERVISOR-LIFE-004
@pytest.mark.slow
def test_one_daemon_per_state_and_a_clean_stop(tmp_path: Path) -> None:
    marker = tmp_path / "working"
    first = _daemon(tmp_path, script={"whoop.range": [{"touch": str(marker)}, {"sleep": 60}]})
    try:
        _wait_running(tmp_path)
        second = _daemon(tmp_path)
        out, _ = second.communicate(timeout=20)
        assert second.returncode == 1
        refused = [e for e in _events(out) if e["event"] == "daemon_not_started"]
        assert refused and "another" in refused[0]["action"].lower()
        from proc_helpers import wait_for

        wait_for(marker)
        first.terminate()
        out, err = first.communicate(timeout=20)
        assert first.returncode == 0, err
        assert "daemon_stopping" in [e["event"] for e in _events(out)]
        assert "range" in [u["job"] for u in state_doc(tmp_path, "inflight.json")["units"]]
    finally:
        first.kill()


# @spec SUPERVISOR-LIFE-002
@pytest.mark.slow
def test_invalid_configuration_exits_2_before_touching_state(tmp_path: Path) -> None:
    proc = _daemon(tmp_path, env={"WEARSTREAM_FAILING_TICKS": "0"})
    out, _ = proc.communicate(timeout=20)
    assert proc.returncode == 2
    assert list((tmp_path / "state").iterdir()) == []
    assert "config_invalid" in [e["event"] for e in _events(out)]


# @spec SUPERVISOR-LIFE-002
@pytest.mark.slow
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_an_unwritable_state_folder_stops_startup(tmp_path: Path) -> None:
    dirs(tmp_path)
    (tmp_path / "state").chmod(0o555)
    try:
        proc = _daemon(tmp_path)
        out, _ = proc.communicate(timeout=20)
    finally:
        (tmp_path / "state").chmod(0o755)
    assert proc.returncode == 1
    assert [e for e in _events(out) if e["event"] == "daemon_not_started"][0]["action"]
