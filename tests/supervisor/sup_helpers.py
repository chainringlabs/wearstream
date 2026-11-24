"""Helpers shared by the supervisor tests: a fake clock, a fake spawner, an in-process fake
source, and ways to build settings and read `/state`."""

from __future__ import annotations

import datetime as dt
import json
import signal
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from wearstream.settings import load

START = dt.datetime(2026, 11, 23, 10, 0, tzinfo=dt.UTC)  # a Monday, 10:00 UTC
COLLECTIONS = {
    ("whoop", "range"): ("sleep", "recovery", "workout", "cycle"),
    ("garmin", "daily"): ("sleep", "stress"),
}


class FakeClock:
    def __init__(self, start: dt.datetime = START) -> None:
        self.wall = start
        self.mono = 1000.0
        self.slept: list[float] = []

    def now(self) -> dt.datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self.wall += dt.timedelta(seconds=seconds)
        self.mono += seconds

    def set(self, when: dt.datetime) -> None:
        self.advance((when - self.wall).total_seconds())


class FakeHandle:
    _next_pid = 50_000

    def __init__(self, unit: Any, job: Any) -> None:
        FakeHandle._next_pid += 1
        self.pid = FakeHandle._next_pid
        self.unit, self.job = unit, job
        self.code: int | None = None
        self._result: dict[str, Any] | None = None
        self.signals: list[int] = []

    def poll(self) -> int | None:
        return self.code

    def result(self) -> dict[str, Any] | None:
        return self._result

    def signal(self, sig: int) -> None:
        self.signals.append(sig)

    def finish(self, code: int = 0, result: Any = "default") -> None:
        self.code = code
        if result == "default":
            result = ok_result() if code == 0 else None
        self._result = result


class FakeSpawner:
    def __init__(self) -> None:
        self.handles: list[FakeHandle] = []

    def __call__(self, unit: Any, job: Any) -> FakeHandle:
        handle = FakeHandle(unit, job)
        self.handles.append(handle)
        return handle

    def live(self) -> list[FakeHandle]:
        return [h for h in self.handles if h.code is None]

    def started(self) -> list[tuple[str, str, str | None, str]]:
        return [(h.unit.source, h.unit.job, h.unit.day, h.unit.trigger) for h in self.handles]


class FakeSource:
    """What the supervisor asks a source for: collections and a credential fingerprint."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fp = "fp-1"
        self.fingerprint_calls = 0

    def collections(self, job: str, values: dict[str, Any]) -> tuple[str, ...]:
        return COLLECTIONS.get((self.name, job), ())

    def fingerprint(self, paths: Any) -> str:
        self.fingerprint_calls += 1
        return self.fp

    def run(self, ctx: Any) -> None:  # pragma: no cover - the daemon never runs units in-process
        raise AssertionError("units run in child processes")


class Inline:
    """An executor that runs work immediately, so background scans are deterministic."""

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future:
        future: Future = Future()
        future.set_result(fn(*args))
        return future


def ok_result(written: int = 1) -> dict[str, Any]:
    return {
        "written": written, "skipped_identical": 0, "skipped_non_2xx": 0, "failed": 0,
        "error": None, "environment": None, "needs_credential": False, "fingerprint": None,
        "rate_limited": False, "retry_after_seconds": None,
    }  # fmt: skip


def result(**changes: Any) -> dict[str, Any]:
    return {**ok_result(0), **changes}


def dirs(tmp_path: Path) -> dict[str, str]:
    env = {}
    for name in ("data", "state", "secrets"):
        (tmp_path / name).mkdir(exist_ok=True)
        env[f"WEARSTREAM_{name.upper()}_DIR"] = str(tmp_path / name)
    return env


def values(tmp_path: Path, sources: str = "whoop", **settings: str) -> dict[str, Any]:
    loaded = load({**dirs(tmp_path), "WEARSTREAM_SOURCES": sources, **settings}, "daemon")
    assert loaded.problems == [], loaded.problems
    return loaded.values


def state_doc(tmp_path: Path, relative: str) -> Any:
    return json.loads((tmp_path / "state" / relative).read_text())


def run_log(tmp_path: Path) -> list[dict[str, Any]]:
    lines = []
    for path in sorted((tmp_path / "state" / "runs").glob("*.jsonl")):
        lines += [json.loads(line) for line in path.read_text().splitlines() if line]
    return lines


TERM, KILL = signal.SIGTERM, signal.SIGKILL


class Logs:
    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        parsed = [json.loads(line) for line in self.lines]
        return [e for e in parsed if name is None or e["event"] == name]

    def names(self) -> list[str]:
        return [e["event"] for e in self.events()]


def iso(moment: dt.datetime) -> str:
    return (
        moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{moment.microsecond // 1000:03d}Z"
    )


def at(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.UTC)


def seed(tmp_path: Path, relative: str, doc: Any) -> None:
    path = tmp_path / "state" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc) if not isinstance(doc, str) else doc)


def seed_ticks(
    tmp_path: Path,
    when: dt.datetime,
    jobs: tuple[str, ...] = ("whoop.range", "whoop.snapshots", "whoop.checks"),
) -> None:
    """Record each listed job as having completed a tick at `when` (no catch-up at startup)."""
    for job in jobs:
        seed(
            tmp_path,
            f"jobs/{job}.json",
            {
                "last_tick": iso(when),
                "last_completed_tick": iso(when),
                "consecutive_failed_ticks": 0,
                "failing": False,
            },
        )


def drive(
    supervisor: Any,
    spawner: FakeSpawner,
    code: Callable[[FakeHandle], int | tuple[int, dict]] = lambda h: 0,
    passes: int = 200,
) -> None:
    """Step until nothing is queued or running, finishing each child as `code` says."""
    for _ in range(passes):
        supervisor.step()
        live = spawner.live()
        if not live and supervisor.idle():
            return
        for handle in live:
            outcome = code(handle)
            handle.finish(*outcome) if isinstance(outcome, tuple) else handle.finish(outcome)
    raise AssertionError("the supervisor never went idle")
