"""Fixtures for the capture tests: a data root, a fresh index, logs, a syscall spy."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from capture_helpers import Logs, Syscalls, install_spy

from wearstream import log
from wearstream.capture import clear_index


@pytest.fixture
def root(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    return data


@pytest.fixture(autouse=True)
def fresh_index() -> Iterator[None]:
    clear_index()
    yield
    clear_index()


@pytest.fixture
def logs() -> Logs:
    captured = Logs()
    log.configure(fmt="json", level="DEBUG", write=captured.lines.append)
    return captured


@pytest.fixture
def syscalls(monkeypatch: pytest.MonkeyPatch) -> Syscalls:
    return install_spy(monkeypatch)
