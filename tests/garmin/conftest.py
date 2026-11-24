"""Fixtures for the Garmin tests: the folders, a fake clock and a stand-in library wired into
the source, a saved session, and a unit's context."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from garmin_helpers import EMAIL, PASSWORD, SESSION, FakeClock, FakeGarmin, FakeLibrary, Logs
from garmin_helpers import write_session as _write_session

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
    (tmp_path / "secrets" / "garmin").mkdir(mode=0o700)
    return found


@pytest.fixture
def folder(env: dict[str, str]) -> Path:
    return Path(env["WEARSTREAM_SECRETS_DIR"]) / "garmin"


@pytest.fixture
def data(env: dict[str, str]) -> Path:
    return Path(env["WEARSTREAM_DATA_DIR"])


@pytest.fixture
def state(env: dict[str, str]) -> Path:
    return Path(env["WEARSTREAM_STATE_DIR"])


@pytest.fixture(autouse=True)
def mounted(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """`/secrets` is a mount point, as in a real deployment (tests run on one volume)."""
    secrets = Path(env["WEARSTREAM_SECRETS_DIR"])
    monkeypatch.setattr("wearstream.garmin.store.is_mount", lambda p: Path(p) == secrets)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Every Garmin module reads time through `wearstream.garmin.clock`."""
    fake = FakeClock()
    monkeypatch.setattr("wearstream.garmin.clock.utc_now", fake.utc_now)
    monkeypatch.setattr("wearstream.garmin.clock.monotonic", fake.monotonic)
    monkeypatch.setattr("wearstream.garmin.clock.sleep", fake.sleep)
    return fake


@pytest.fixture
def garmin(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> FakeLibrary:
    """WearStream builds every client through `wearstream.garmin.library.Garmin`."""
    library = FakeLibrary(clock)
    FakeGarmin.library = library
    monkeypatch.setattr("wearstream.garmin.library.Garmin", FakeGarmin)
    return library


@pytest.fixture
def session(folder: Path) -> Path:
    return _write_session(folder, SESSION)


@pytest.fixture
def credentials(folder: Path) -> None:
    (folder / "email").write_text(EMAIL + "\n")
    (folder / "password").write_text(PASSWORD + "\n")


@pytest.fixture
def logs() -> Logs:
    captured = Logs()
    log.configure(fmt="json", level="DEBUG", write=captured.lines.append)
    return captured


@pytest.fixture
def unit(env: dict[str, str], logs: Logs) -> Callable[..., RunContext]:
    def build(
        job: str = "daily",
        day: str | None = "2026-11-20",
        trigger: str = "schedule",
        time_limit: float | None = None,
        **settings: str,
    ) -> RunContext:
        loaded = load({**env, "WEARSTREAM_SOURCES": "garmin", **settings}, "run")
        assert loaded.problems == [], loaded.problems
        chosen = effective_jobs(loaded.values)["garmin", job]
        if time_limit is not None:
            chosen = dataclasses.replace(chosen, time_limit=time_limit)
        logger = log.get_logger("run").bind(source="garmin", job=job, trigger=trigger)
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
    def go(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        ctx = unit(*args, **kwargs)
        code = execute(ctx, ctx.log)
        return code, ctx.result()

    return go
