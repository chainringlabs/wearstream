"""Helpers for supervisor tests that run real `wearstream` processes through the fake launcher."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sup_helpers import dirs


def coverage_env() -> dict[str, str]:
    """Coverage's settings, so the processes a test starts are measured too."""
    import os

    return {k: v for k, v in os.environ.items() if k.startswith("COVERAGE_")}


LAUNCHER = str(Path(__file__).with_name("fake_wearstream.py"))


class Proc:
    """A real `wearstream` process run through the fake launcher."""

    def __init__(
        self,
        tmp_path: Path,
        args: list[str],
        script: dict[str, Any],
        env: dict[str, str] | None = None,
        result_pipe: bool = True,
    ) -> None:
        import os
        import subprocess
        import sys

        script_path = tmp_path / "script.json"
        script_path.write_text(json.dumps(script))
        full_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), **dirs(tmp_path),
            "WEARSTREAM_SOURCES": "whoop,garmin", "WEARSTREAM_LOG_FORMAT": "json",
            "FAKE_SOURCE_SCRIPT": str(script_path), **coverage_env(), **(env or {}),
        }  # fmt: skip
        self.read_fd: int | None = None
        argv = [sys.executable, LAUNCHER, *args]
        pass_fds: tuple[int, ...] = ()
        if result_pipe:
            self.read_fd, write_fd = os.pipe()
            argv += ["--result-fd", str(write_fd)]
            pass_fds = (write_fd,)
        self.process = subprocess.Popen(
            argv,
            env=full_env,
            pass_fds=pass_fds,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if result_pipe:
            os.close(write_fd)
        self.result: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.stderr = ""

    def wait(self, timeout: float = 30) -> int:
        import os

        out, err = self.process.communicate(timeout=timeout)
        self.stderr = err.decode()
        self.events = [
            json.loads(line) for line in out.decode().splitlines() if line.startswith("{")
        ]
        if self.read_fd is not None:
            data = b""
            while chunk := os.read(self.read_fd, 65536):
                data += chunk
            os.close(self.read_fd)
            self.result = json.loads(data) if data else None
        return self.process.returncode

    def named(self, event: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event"] == event]


def wait_for(path: Path, text: str | None = None, timeout: float = 15) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and (text is None or path.read_text() == text):
            return
        time.sleep(0.02)
    raise AssertionError(f"{path} never reached {text!r}")


def daemon_args(
    source: str, job: str, day: str | None = None, trigger: str = "schedule", run_id: str = "r1"
) -> list[str]:
    args = ["run", source, job] + (["--day", day] if day else [])
    return args + ["--run-id", run_id, "--trigger", trigger]


def hold_lock(path: Path) -> int:
    import fcntl
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd
