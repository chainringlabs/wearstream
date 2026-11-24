"""Starting `wearstream run` children, reading their result pipes, and the real clock."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import threading
import time
from typing import Any

import wearstream.supervisor as pkg
from wearstream.supervisor.units import Unit

RESULT_LIMIT = 64 * 1024


class SystemClock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class ProcessHandle:
    """A running child: its exit code, its process group, and its result."""

    def __init__(self, unit: Unit, process: subprocess.Popen, read_fd: int) -> None:
        self.unit, self.process, self.pid = unit, process, process.pid
        self._buffer = bytearray()
        self._overflow = False
        self._reader = threading.Thread(target=self._read, args=(read_fd,), daemon=True)
        self._reader.start()

    # @spec SUPERVISOR-OUTCOME-001
    def _read(self, fd: int) -> None:
        try:
            while chunk := os.read(fd, 65536):
                if len(self._buffer) + len(chunk) > RESULT_LIMIT:
                    self._overflow = True
                else:
                    self._buffer += chunk
        except OSError:
            pass
        finally:
            os.close(fd)

    def poll(self) -> int | None:
        return self.process.poll()

    def partial(self) -> Any:
        try:
            return json.loads(bytes(self._buffer))
        except ValueError:
            return None

    def result(self) -> Any:
        self._reader.join(timeout=2)
        return None if self._overflow else self.partial()

    def signal(self, sig: int) -> None:
        try:
            os.killpg(self.pid, sig)
        except ProcessLookupError, PermissionError:
            pass


class ProcessSpawner:
    """Starts a unit as `wearstream run …` in a new process group with a result pipe."""

    def __init__(self, prefix: list[str] | None = None) -> None:
        self.prefix = prefix

    # @spec SUPERVISOR-CHILD-001
    def __call__(self, unit: Unit, job: Any) -> ProcessHandle:
        read_fd, write_fd = os.pipe()
        argv = [*(self.prefix or pkg.CHILD_PREFIX), "run", unit.source, unit.job]
        argv += ["--day", unit.day] if unit.day else []
        argv += ["--run-id", unit.run_id, "--trigger", unit.trigger, "--result-fd", str(write_fd)]
        try:
            process = subprocess.Popen(
                argv, pass_fds=(write_fd,), start_new_session=True, stdin=subprocess.DEVNULL
            )
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        return ProcessHandle(unit, process, read_fd)
