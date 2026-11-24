"""Starting a child: argv, process group, result pipe (SUPERVISOR-CHILD-001, -OUTCOME-001)."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from wearstream.supervisor import ProcessSpawner, Unit

CHILD = """
import json, os, sys, subprocess, time
args = sys.argv[1:]
fd = int(args[args.index("--result-fd") + 1])
mode = os.environ.get("MODE", "report")
if mode == "report":
    os.write(fd, json.dumps({"argv": args, "own_group": os.getpgid(0) == os.getpid()}).encode())
elif mode == "flood":
    os.write(fd, b"x" * 100_000)
elif mode == "grandchild":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    os.write(fd, json.dumps({"grandchild": child.pid}).encode())
    time.sleep(60)
sys.exit(int(os.environ.get("CODE", "0")))
"""


def _wait(handle: object, timeout: float = 15) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = handle.poll()  # type: ignore[attr-defined]
        if code is not None:
            return code
        time.sleep(0.02)
    raise AssertionError("child never exited")


@pytest.fixture
def spawner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProcessSpawner:
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    return ProcessSpawner(prefix=[sys.executable, str(script)])


# @spec SUPERVISOR-CHILD-001
def test_a_child_gets_its_unit_and_its_own_process_group(spawner: ProcessSpawner) -> None:
    handle = spawner(Unit("whoop", "range", "2026-11-20", "schedule", "r1"), None)
    assert _wait(handle) == 0
    reported = handle.result()
    argv = reported["argv"]
    assert argv[:5] == ["run", "whoop", "range", "--day", "2026-11-20"]
    assert (
        argv[argv.index("--run-id") + 1] == "r1" and argv[argv.index("--trigger") + 1] == "schedule"
    )
    assert reported["own_group"] is True


# @spec SUPERVISOR-CHILD-001
def test_a_snapshot_child_gets_no_day(spawner: ProcessSpawner) -> None:
    handle = spawner(Unit("whoop", "snapshots", None, "catchup", "r2"), None)
    _wait(handle)
    assert "--day" not in handle.result()["argv"]


# @spec SUPERVISOR-OUTCOME-001
def test_the_result_is_read_while_the_child_runs_and_capped(
    spawner: ProcessSpawner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODE", "flood")
    handle = spawner(Unit("whoop", "snapshots", None, "schedule", "r3"), None)
    assert _wait(handle) == 0
    assert handle.result() is None


# @spec SUPERVISOR-OUTCOME-001
def test_an_exit_code_is_reported(spawner: ProcessSpawner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE", "4")
    handle = spawner(Unit("whoop", "snapshots", None, "schedule", "r4"), None)
    assert _wait(handle) == 4


# @spec SUPERVISOR-LIMIT-002
def test_signals_reach_the_whole_process_group(
    spawner: ProcessSpawner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODE", "grandchild")
    handle = spawner(Unit("whoop", "snapshots", None, "schedule", "r5"), None)
    deadline = time.monotonic() + 10
    while handle.partial() is None and time.monotonic() < deadline:  # type: ignore[attr-defined]
        time.sleep(0.02)
    grandchild = handle.partial()["grandchild"]  # type: ignore[attr-defined]
    handle.signal(signal.SIGKILL)
    assert _wait(handle) == -signal.SIGKILL
    for _ in range(100):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the grandchild survived")
