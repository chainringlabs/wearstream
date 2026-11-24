"""JSON documents under `/state`, written atomically and updated under `state.lock`."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from wearstream.log import get_logger

KEPT_MONTHS = 13
_MONTH_FILE = re.compile(r"\d{4}-\d{2}\.jsonl")
Doc = dict[str, Any]


def iso(moment: dt.datetime) -> str:
    moment = moment.astimezone(dt.UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_time(text: object) -> dt.datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.UTC)


def _fsync_folder(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class StateStore:
    """Documents are plain JSON objects; a missing or unreadable one is treated as absent."""

    def __init__(self, root: Path | str, established: bool = False) -> None:
        self.root = Path(root)
        self.established = established
        self.lock_wait = 10.0
        self.log = get_logger("state")

    def path(self, relative: str) -> Path:
        return self.root / relative

    def read_quiet(self, relative: str) -> Doc | None:
        try:
            doc = json.loads(self.path(relative).read_bytes())
        except OSError, ValueError:
            return None
        return doc if isinstance(doc, dict) else None

    # @spec SUPERVISOR-STATE-005
    def read(self, relative: str) -> Doc | None:
        path = self.path(relative)
        if not path.exists():
            if self.established:
                self.log.warning("state_document_reset", path=str(path), reason="missing")
            return None
        doc = self.read_quiet(relative)
        if doc is None:
            self.log.warning("state_document_reset", path=str(path), reason="unreadable")
        return doc

    # @spec SUPERVISOR-STATE-001
    def write(self, relative: str, doc: Doc) -> None:
        """Temporary file, fsync, rename into place, fsync the folder."""
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.{secrets.token_hex(4)}.tmp"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                os.write(fd, (json.dumps(doc, sort_keys=True) + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        _fsync_folder(target.parent)

    @contextmanager
    def state_lock(self) -> Iterator[bool]:
        """Hold `locks/state.lock`, waiting up to `lock_wait` seconds; yields whether it is held."""
        locks = self.root / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            locks / "state.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o644
        )
        deadline = time.monotonic() + self.lock_wait
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        yield False
                        return
                    time.sleep(0.05)
            yield True
        finally:
            os.close(fd)

    # @spec SUPERVISOR-STATE-002
    def update(self, relative: str, change: Callable[[Doc | None], Doc]) -> Doc | None:
        """Read, change and write one document under `state.lock` (checks documents excepted)."""
        if relative.startswith("checks/"):
            doc = change(self.read(relative))
            self.write(relative, doc)
            return doc
        with self.state_lock() as held:
            if not held:
                self.log.warning("state_update_skipped", path=str(self.path(relative)))
                return None
            doc = change(self.read(relative))
            self.write(relative, doc)
            return doc

    # @spec SUPERVISOR-STATE-004
    def append_run(self, line: Doc, now: dt.datetime) -> None:
        """Append one line to this month's run log and keep the 13 most recent months."""
        runs = self.root / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        fd = os.open(runs / f"{now:%Y-%m}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps({"at": iso(now), **line}, sort_keys=True) + "\n").encode())
        finally:
            os.close(fd)
        months = sorted(p for p in runs.iterdir() if _MONTH_FILE.fullmatch(p.name))
        for old in months[:-KEPT_MONTHS]:
            old.unlink(missing_ok=True)

    def run_lines(self, months: list[str]) -> list[Doc]:
        """The run log's lines for the given `YYYY-MM` months, skipping unreadable lines."""
        lines: list[Doc] = []
        for month in months:
            try:
                text = (self.root / "runs" / f"{month}.jsonl").read_text()
            except OSError:
                continue
            for raw in text.splitlines():
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    lines.append(parsed)
        return lines
