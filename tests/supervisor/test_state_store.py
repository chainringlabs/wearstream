"""Atomic documents, `state.lock`, the run log and unreadable documents (SUPERVISOR-STATE)."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
from typing import Any

import pytest
from sup_helpers import Logs

from wearstream.state import StateStore

NOW = dt.datetime(2026, 11, 23, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def store(tmp_path: Path, logs: Logs) -> StateStore:
    (tmp_path / "state").mkdir()
    return StateStore(tmp_path / "state")


# @spec SUPERVISOR-STATE-001
def test_documents_are_written_atomically(
    store: StateStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    fds: dict[int, str] = {}
    real_open, real_fsync, real_replace = os.open, os.fsync, os.replace

    def spy_open(path: Any, flags: int, *args: Any, **kw: Any) -> int:
        fd = real_open(path, flags, *args, **kw)
        fds[fd] = os.fspath(path)
        return fd

    def spy_fsync(fd: int) -> None:
        calls.append(("fsync", fds.get(fd, "?")))
        real_fsync(fd)

    def spy_replace(src: Any, dst: Any, *args: Any, **kw: Any) -> None:
        calls.append(("replace", os.fspath(dst)))
        real_replace(src, dst, *args, **kw)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)
    store.write("jobs/whoop.range.json", {"last_tick": "x"})
    target = str(tmp_path / "state" / "jobs" / "whoop.range.json")
    replaced = calls.index(("replace", target))
    assert any(
        kind == "fsync" and Path(path).name.startswith(".") for kind, path in calls[:replaced]
    )
    assert ("fsync", str(tmp_path / "state" / "jobs")) in calls[replaced:]
    assert json.loads(Path(target).read_text()) == {"last_tick": "x"}
    assert [p.name for p in Path(target).parent.iterdir()] == ["whoop.range.json"]


# @spec SUPERVISOR-STATE-001, SUPERVISOR-STATE-002
def test_update_reads_modifies_and_writes(store: StateStore) -> None:
    assert store.update("jobs/a.b.json", lambda doc: {"n": (doc or {}).get("n", 0) + 1}) == {"n": 1}
    assert store.update("jobs/a.b.json", lambda doc: {"n": doc["n"] + 1}) == {"n": 2}
    assert store.read("jobs/a.b.json") == {"n": 2}


# @spec SUPERVISOR-STATE-002
def test_an_update_waits_for_state_lock_and_skips_after_the_timeout(
    store: StateStore, tmp_path: Path, logs: Logs
) -> None:
    store.write("jobs/a.b.json", {"n": 1})
    store.lock_wait = 0.3
    (tmp_path / "state" / "locks").mkdir(exist_ok=True)
    holder = os.open(tmp_path / "state" / "locks" / "state.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        assert store.update("jobs/a.b.json", lambda doc: {"n": 99}) is None
    finally:
        os.close(holder)
    assert store.read("jobs/a.b.json") == {"n": 1}
    [skipped] = logs.events("state_update_skipped")
    assert skipped["level"] == "warning"


# @spec SUPERVISOR-STATE-002
def test_checks_documents_do_not_use_state_lock(store: StateStore, tmp_path: Path) -> None:
    store.lock_wait = 0.3
    (tmp_path / "state" / "locks").mkdir(exist_ok=True)
    holder = os.open(tmp_path / "state" / "locks" / "state.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        assert store.update("checks/whoop/baselines.json", lambda doc: {"ok": True}) == {"ok": True}
    finally:
        os.close(holder)


# @spec SUPERVISOR-STATE-004
def test_run_lines_go_to_the_months_file(store: StateStore, tmp_path: Path) -> None:
    store.append_run({"type": "start", "run_id": "r1"}, NOW)
    store.append_run(
        {"type": "finish", "run_id": "r1", "outcome": "success"}, NOW + dt.timedelta(days=8)
    )
    november = (tmp_path / "state" / "runs" / "2026-11.jsonl").read_text().splitlines()
    december = (tmp_path / "state" / "runs" / "2026-12.jsonl").read_text().splitlines()
    assert [json.loads(line)["type"] for line in november] == ["start"]
    assert json.loads(december[0])["outcome"] == "success"


# @spec SUPERVISOR-STATE-004
def test_only_the_13_most_recent_monthly_files_are_kept(store: StateStore, tmp_path: Path) -> None:
    runs = tmp_path / "state" / "runs"
    runs.mkdir()
    months = [
        f"{2025 + (m - 1) // 12}-{(m - 1) % 12 + 1:02d}" for m in range(1, 23)
    ]  # 2025-01..2026-10
    for month in months:
        (runs / f"{month}.jsonl").write_text("{}\n")
    (runs / "notes.txt").write_text("not a run log")
    store.append_run({"type": "start", "run_id": "r1"}, NOW)
    kept = sorted(p.stem for p in runs.glob("*.jsonl"))
    assert kept == months[-12:] + ["2026-11"]
    assert (runs / "notes.txt").exists()


# @spec SUPERVISOR-STATE-005
def test_an_unparseable_document_is_reported_and_treated_as_absent(
    store: StateStore, tmp_path: Path, logs: Logs
) -> None:
    (tmp_path / "state" / "jobs").mkdir()
    (tmp_path / "state" / "jobs" / "whoop.range.json").write_text("{not json")
    assert store.read("jobs/whoop.range.json") is None
    [reset] = logs.events("state_document_reset")
    assert reset["level"] == "warning" and "whoop.range.json" in reset["path"]


# @spec SUPERVISOR-STATE-005
def test_missing_documents_are_reported_only_on_an_established_state(
    tmp_path: Path, logs: Logs
) -> None:
    (tmp_path / "state").mkdir()
    assert StateStore(tmp_path / "state").read("jobs/whoop.range.json") is None
    assert logs.events("state_document_reset") == []
    assert StateStore(tmp_path / "state", established=True).read("jobs/whoop.range.json") is None
    assert len(logs.events("state_document_reset")) == 1


# @spec SUPERVISOR-STATE-005
def test_a_document_that_is_not_an_object_is_treated_as_absent(
    store: StateStore, tmp_path: Path
) -> None:
    (tmp_path / "state" / "heartbeat.json").write_text("[1, 2]")
    assert store.read("heartbeat.json") is None
