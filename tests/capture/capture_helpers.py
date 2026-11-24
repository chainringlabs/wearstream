"""Helpers shared by the capture tests: sample metadata, a log collector, a syscall spy, and
functions that place files in the tree the way another tool would."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

META: dict[str, Any] = {
    "http_status": 200,
    "content_type": "application/json",
    "request_url": "https://api.example.test/v2/activity/sleep",
    "request_params": {"start": "2026-11-20T00:00:00Z"},
}
DAY = "2026-11-20"


class Logs:
    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        parsed = [json.loads(line) for line in self.lines]
        return [e for e in parsed if name is None or e["event"] == name]


class Syscalls:
    """Records os.open/fsync/link/unlink in order; fsyncs are reported by the path opened."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fds: dict[int, str] = {}

    def of(self, kind: str) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] == kind]


def install_spy(monkeypatch: pytest.MonkeyPatch) -> Syscalls:
    spy = Syscalls()
    real_open, real_fsync, real_link, real_unlink = os.open, os.fsync, os.link, os.unlink

    def spy_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        spy.fds[fd] = os.fspath(path)
        spy.calls.append(("open", os.fspath(path)))
        return fd

    def spy_fsync(fd: int) -> None:
        spy.calls.append(("fsync", spy.fds.get(fd, "?")))
        real_fsync(fd)

    def spy_link(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        spy.calls.append(("link", os.fspath(src), os.fspath(dst)))
        real_link(src, dst, *args, **kwargs)

    def spy_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        spy.calls.append(("unlink", os.fspath(path)))
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "link", spy_link)
    monkeypatch.setattr(os, "unlink", spy_unlink)
    return spy


def put(folder: Path, name: str, data: bytes | str = b"{}") -> Path:
    """Place a file directly in the tree, as another tool or an older writer would."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def put_capture(folder: Path, stem: str, meta: dict[str, Any], payload: bytes = b"{}") -> None:
    """A complete pair: `{stem}.json` and its sidecar."""
    put(folder, f"{stem}.json", payload)
    put(folder, f"{stem}.meta.json", json.dumps(meta, separators=(",", ":"), sort_keys=True))


def tree(root: Path) -> list[str]:
    """Every file under root, relative, sorted."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
