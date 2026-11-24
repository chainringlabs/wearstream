"""Fixtures for the supervisor tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sup_helpers import FakeClock, FakeSource, FakeSpawner, Inline, Logs, at, seed_ticks, values

from wearstream import log, sources
from wearstream.supervisor import Supervisor


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def spawner() -> FakeSpawner:
    return FakeSpawner()


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeSource]:
    made = {name: FakeSource(name) for name in ("whoop", "garmin")}
    for name, source in made.items():
        monkeypatch.setitem(sources.REGISTRY, name, source)
    return made


@pytest.fixture
def logs() -> Logs:
    captured = Logs()
    log.configure(fmt="json", level="DEBUG", write=captured.lines.append)
    return captured


@pytest.fixture
def make(
    tmp_path: Path, clock: FakeClock, spawner: FakeSpawner, fakes: dict, logs: Logs
) -> Callable[..., Supervisor]:
    """Build a supervisor over tmp_path's data/state/secrets with fake time and children."""

    def build(sources: str = "whoop", **settings: str) -> Supervisor:
        return Supervisor(
            values(tmp_path, sources, **settings), clock=clock, spawn=spawner, executor=Inline()
        )

    return build


@pytest.fixture
def started(make: Callable[..., Supervisor]) -> Supervisor:
    """A Whoop-only supervisor that has started and made its first loop pass."""
    supervisor = make()
    supervisor.start()
    supervisor.step()
    return supervisor


@pytest.fixture
def quiet(tmp_path: Path, make: Callable[..., Supervisor]) -> Supervisor:
    """Whoop with every job completed at 09:17, so startup at 10:00 has nothing to catch up."""
    seed_ticks(tmp_path, at("2026-11-23T09:17"))
    supervisor = make()
    supervisor.start()
    supervisor.step()
    return supervisor


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "slow: runs real processes for several seconds")
