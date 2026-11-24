"""Writing a capture: names, folders, extensions, the atomic sequence, permissions
(CAPTURE-WRITE)."""

from __future__ import annotations

import datetime as dt
import os
import re
import time
from pathlib import Path

import pytest
from capture_helpers import DAY, META, Syscalls, put, tree

from wearstream.capture import capture
from wearstream.capture import write as writer

NAME = re.compile(r"^sleep_(\d+)_([0-9a-f]{6})\.json$")


# @spec CAPTURE-WRITE-001, CAPTURE-WRITE-010
def test_a_capture_is_a_payload_and_sidecar_named_by_fetch_time_and_short_id(root: Path) -> None:
    before = int(time.time() * 1000)
    result = capture(root, "whoop", "sleep", b'{"a":1}', META, dt=DAY)
    after = int(time.time() * 1000)
    assert result.outcome == "written"
    assert result.error_kind is None
    path = Path(result.path)
    assert path.parent == root / "whoop" / "sleep" / f"dt={DAY}"
    match = NAME.match(path.name)
    assert match and before <= int(match.group(1)) <= after
    assert path.read_bytes() == b'{"a":1}'
    assert path.with_name(path.name.removesuffix(".json") + ".meta.json").is_file()
    assert len(tree(root)) == 2


# @spec CAPTURE-WRITE-002
@pytest.mark.parametrize("day", [None, ""])
def test_without_a_partition_day_the_folder_is_the_utc_fetch_date(
    root: Path, monkeypatch: pytest.MonkeyPatch, day: str | None
) -> None:
    late_evening = dt.datetime(2026, 11, 23, 23, 30, tzinfo=dt.UTC)
    monkeypatch.setattr(writer, "utc_now_ms", lambda: int(late_evening.timestamp() * 1000))
    result = capture(root, "whoop", "sleep", b"{}", META, dt=day)
    assert Path(result.path).parent.name == "dt=2026-11-23"


# @spec CAPTURE-WRITE-003
@pytest.mark.parametrize(
    ("content_type", "ext"),
    [
        ("application/json; charset=utf-8", "json"),
        (" Text/JSON ", "json"),
        ("text/csv", "csv"),
        ("application/csv", "csv"),
        ("application/xml", "xml"),
        ("text/xml", "xml"),
        ("text/plain", "txt"),
        ("application/zip", "bin"),
        (None, "bin"),
    ],
)
def test_the_extension_comes_from_the_content_type(
    root: Path, content_type: str | None, ext: str
) -> None:
    meta = {k: v for k, v in META.items() if k != "content_type"}
    if content_type is not None:
        meta["content_type"] = content_type
    result = capture(root, "garmin", "sleep", b"x", meta, dt=DAY)
    assert result.path.endswith(f".{ext}")


# @spec CAPTURE-WRITE-003
def test_an_explicit_extension_is_used_unchanged(root: Path) -> None:
    result = capture(root, "garmin", "activity_files", b"PK", META, dt=DAY, ext="fit.zip")
    assert result.path.endswith(".fit.zip")


# @spec CAPTURE-WRITE-004
def test_both_files_are_prepared_and_synced_before_either_is_linked(
    root: Path, syscalls: Syscalls
) -> None:
    capture(root, "whoop", "sleep", b"{}", META, dt=DAY)
    folder = str(root / "whoop" / "sleep" / f"dt={DAY}")
    links = syscalls.of("link")
    assert len(links) == 2
    (_, payload_tmp, payload), (_, sidecar_tmp, sidecar) = links
    for tmp in (payload_tmp, sidecar_tmp):
        assert os.path.dirname(tmp) == folder and os.path.basename(tmp).startswith(".tmp_")
    assert payload.endswith(".json") and not payload.endswith(".meta.json")
    assert sidecar.endswith(".meta.json")
    first_link = syscalls.calls.index(links[0])
    synced_before = [c[1] for c in syscalls.calls[:first_link] if c[0] == "fsync"]
    assert payload_tmp in synced_before and sidecar_tmp in synced_before
    synced_after = [
        c[1] for c in syscalls.calls[syscalls.calls.index(links[1]) :] if c[0] == "fsync"
    ]
    assert folder in synced_after


# @spec CAPTURE-WRITE-005
def test_new_folders_are_made_durable_by_syncing_their_parents(
    root: Path, syscalls: Syscalls
) -> None:
    capture(root, "whoop", "sleep", b"1", META, dt=DAY)
    synced = {c[1] for c in syscalls.of("fsync")}
    for parent in (root, root / "whoop", root / "whoop" / "sleep"):
        assert str(parent) in synced
    syscalls.calls.clear()
    capture(root, "whoop", "sleep", b"2", META, dt=DAY)
    assert str(root / "whoop" / "sleep") not in {c[1] for c in syscalls.of("fsync")}


# @spec CAPTURE-WRITE-006
def test_no_temporary_file_survives_a_success_or_a_failure(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture(root, "whoop", "sleep", b"1", META, dt=DAY)

    def broken_link(*_args: object, **_kw: object) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "link", broken_link)
    assert capture(root, "whoop", "sleep", b"2", META, dt=DAY).outcome == "failed"
    assert not [p for p in root.rglob(".*")]


# @spec CAPTURE-WRITE-011
def test_existing_files_are_never_changed(root: Path) -> None:
    folder = root / "whoop" / "sleep" / f"dt={DAY}"
    stranger = put(folder, "notes.txt", b"mine")
    lone = put(folder, "sleep_1_abcdef.json", b"old")
    os.utime(stranger, (1, 1))
    for payload in (b"1", b"2", b"2"):
        capture(root, "whoop", "sleep", payload, META, dt=DAY)
    assert stranger.read_bytes() == b"mine" and stranger.stat().st_mtime == 1
    assert lone.read_bytes() == b"old"


# @spec CAPTURE-WRITE-012
@pytest.mark.parametrize(
    ("mask", "file_mode", "dir_mode"), [(0o022, 0o644, 0o755), (0o077, 0o600, 0o700)]
)
def test_modes_follow_the_process_umask(
    root: Path, mask: int, file_mode: int, dir_mode: int
) -> None:
    previous = os.umask(mask)
    try:
        result = capture(root, "whoop", "sleep", b"{}", META, dt=DAY)
    finally:
        os.umask(previous)
    path = Path(result.path)
    sidecar = path.with_name(path.name.removesuffix(".json") + ".meta.json")
    assert {path.stat().st_mode & 0o777, sidecar.stat().st_mode & 0o777} == {file_mode}
    for folder in (root / "whoop", root / "whoop" / "sleep", path.parent):
        assert folder.stat().st_mode & 0o777 == dir_mode


# @spec CAPTURE-WRITE-013
def test_an_ordinary_error_is_logged_and_reported_not_raised(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kw: object) -> None:
        raise ValueError("unexpected")

    monkeypatch.setattr(os, "link", broken)
    result = capture(root, "whoop", "sleep", b"{}", META, dt=DAY)
    assert (result.outcome, result.path, result.error_kind) == ("failed", None, "other")


# @spec CAPTURE-WRITE-006, CAPTURE-WRITE-014
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_interrupts_propagate_after_temporary_files_are_removed(
    root: Path, monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]
) -> None:
    def stopped(*_args: object, **_kw: object) -> None:
        raise interrupt()

    monkeypatch.setattr(os, "link", stopped)
    with pytest.raises(interrupt):
        capture(root, "whoop", "sleep", b"{}", META, dt=DAY)
    assert not [p for p in root.rglob(".*")]
    assert tree(root) == []
