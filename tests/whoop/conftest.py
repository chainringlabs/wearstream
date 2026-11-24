"""Fixtures for the Whoop tests: the folders, a fake clock and a fake Whoop wired into the
source, a token, and a unit's context."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from whoop_helpers import FakeClock, FakeWhoop, Logs, write_client, write_token

from wearstream import log
from wearstream.jobs import effective_jobs
from wearstream.runner.context import RunContext, Signals
from wearstream.runner.run import execute
from wearstream.settings import load, paths


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    found = {}
    for name in ("data", "state", "secrets"):
        (tmp_path / name).mkdir()
        found[f"WEARSTREAM_{name.upper()}_DIR"] = str(tmp_path / name)
        monkeypatch.setenv(f"WEARSTREAM_{name.upper()}_DIR", str(tmp_path / name))
    (tmp_path / "state" / "locks").mkdir()
    return found


@pytest.fixture
def folder(env: dict[str, str]) -> Path:
    """`/secrets/whoop`, holding the client files."""
    whoop = Path(env["WEARSTREAM_SECRETS_DIR"]) / "whoop"
    write_client(whoop)
    return whoop


@pytest.fixture
def data(env: dict[str, str]) -> Path:
    return Path(env["WEARSTREAM_DATA_DIR"])


@pytest.fixture
def state(env: dict[str, str]) -> Path:
    return Path(env["WEARSTREAM_STATE_DIR"])


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Every Whoop module reads time through `wearstream.whoop.clock`."""
    fake = FakeClock()
    monkeypatch.setattr("wearstream.whoop.clock.utc_now", fake.utc_now)
    monkeypatch.setattr("wearstream.whoop.clock.monotonic", fake.monotonic)
    monkeypatch.setattr("wearstream.whoop.clock.sleep", fake.sleep)
    return fake


@pytest.fixture
def whoop(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> FakeWhoop:
    """Every Whoop HTTP client uses `wearstream.whoop.http.TRANSPORT` when it is set."""
    fake = FakeWhoop(clock)
    monkeypatch.setattr("wearstream.whoop.http.TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def token(folder: Path, clock: FakeClock) -> dict[str, Any]:
    return write_token(folder, clock)


@pytest.fixture
def logs() -> Logs:
    captured = Logs()
    log.configure(fmt="json", level="DEBUG", write=captured.lines.append)
    return captured


@pytest.fixture
def unit(env: dict[str, str], logs: Logs) -> Callable[..., RunContext]:
    """A Whoop unit's context, as the daemon's child or a hand-started run builds it."""

    def build(
        job: str = "range",
        day: str | None = "2026-11-24",
        trigger: str = "schedule",
        time_limit: float | None = None,
        **settings: str,
    ) -> RunContext:
        loaded = load({**env, "WEARSTREAM_SOURCES": "whoop", **settings}, "run")
        assert loaded.problems == [], loaded.problems
        chosen = effective_jobs(loaded.values)["whoop", job]
        if time_limit is not None:
            chosen = dataclasses.replace(chosen, time_limit=time_limit)
        logger = log.get_logger("run").bind(source="whoop", job=job, trigger=trigger)
        return RunContext(
            chosen,
            day if chosen.kind == "partitioned" else None,
            trigger,
            "r1",
            loaded.values,
            paths(loaded.values),
            logger,
            Signals(),
        )

    return build


@pytest.fixture
def run(unit: Callable[..., RunContext]) -> Callable[..., tuple[int, dict[str, Any]]]:
    """Run a unit through the child's `execute`; returns its exit code and result."""

    def go(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        ctx = unit(*args, **kwargs)
        code = execute(ctx, ctx.log)
        return code, ctx.result()

    return go
