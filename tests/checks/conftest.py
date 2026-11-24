"""Fixtures for the checks tests: the three folders, a log collector, a fake source, and a
checks unit's context."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from checks_helpers import NOW, Logs

from wearstream import log, sources
from wearstream.jobs import effective_jobs
from wearstream.runner.context import RunContext, Signals
from wearstream.settings import load, paths


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The three folders, set in the environment as a command would see them."""
    found = {}
    for name in ("data", "state", "secrets"):
        (tmp_path / name).mkdir()
        found[f"WEARSTREAM_{name.upper()}_DIR"] = str(tmp_path / name)
        monkeypatch.setenv(f"WEARSTREAM_{name.upper()}_DIR", str(tmp_path / name))
    return found


@pytest.fixture
def data(env: dict[str, str]) -> Path:
    return Path(env["WEARSTREAM_DATA_DIR"])


@pytest.fixture
def state(env: dict[str, str]) -> Path:
    return Path(env["WEARSTREAM_STATE_DIR"])


@pytest.fixture
def logs() -> Logs:
    captured = Logs()
    log.configure(fmt="json", level="DEBUG", write=captured.lines.append)
    return captured


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checks units read the wall clock through `wearstream.checks.unit.utc_now`."""
    monkeypatch.setattr("wearstream.checks.unit.utc_now", lambda: NOW)


class FakeSource:
    """A source whose check settings and own checks a test chooses."""

    def __init__(self) -> None:
        self.settings: list[Any] = []
        self.own: list[Any] = []

    def collections(self, job: str, values: dict[str, Any]) -> tuple[str, ...]:
        return ()

    def fingerprint(self, paths: Any) -> str:
        return "fp"

    def check_settings(self, values: dict[str, Any]) -> list[Any]:
        return list(self.settings)

    def own_checks(self, values: dict[str, Any], paths: Any) -> list[Any]:
        return list(self.own)

    def run(self, ctx: Any) -> None:
        from wearstream.checks import run_unit

        run_unit(ctx, self.check_settings(ctx.values), self.own_checks(ctx.values, ctx.paths))


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSource:
    made = FakeSource()
    monkeypatch.setitem(sources.REGISTRY, "whoop", made)
    return made


@pytest.fixture
def unit_ctx(env: dict[str, str], logs: Logs) -> Callable[..., RunContext]:
    """A Whoop `checks` unit's context, as the daemon's child (or a hand-started run) builds it."""

    def build(trigger: str = "schedule", time_limit: float | None = None) -> RunContext:
        loaded = load({**env, "WEARSTREAM_SOURCES": "whoop"}, "run")
        assert loaded.problems == [], loaded.problems
        job = effective_jobs(loaded.values)["whoop", "checks"]
        if time_limit is not None:
            job = dataclasses.replace(job, time_limit=time_limit)
        logger = log.get_logger("run").bind(source="whoop", job="checks", trigger=trigger)
        return RunContext(
            job, None, trigger, "r1", loaded.values, paths(loaded.values), logger, Signals()
        )

    return build
