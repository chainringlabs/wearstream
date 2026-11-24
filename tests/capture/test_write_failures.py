"""Name collisions, rolled-back payloads, folder-sync failures and failure kinds
(CAPTURE-WRITE-007..009, CAPTURE-FAIL-001)."""

from __future__ import annotations

import errno
import itertools
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from capture_helpers import DAY, META, Logs, Syscalls, put, tree

from wearstream.capture import capture
from wearstream.capture import write as writer

MS = 1_763_600_000_000


@pytest.fixture
def folder(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(writer, "utc_now_ms", lambda: MS)
    return root / "whoop" / "sleep" / f"dt={DAY}"


def _ids(monkeypatch: pytest.MonkeyPatch, *ids: str) -> None:
    sequence: Iterator[str] = itertools.chain(ids, itertools.repeat(ids[-1]))
    monkeypatch.setattr(writer, "new_short_id", lambda: next(sequence))


# @spec CAPTURE-WRITE-007
def test_a_payload_name_collision_draws_a_new_short_id(
    root: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = put(folder, f"sleep_{MS}_aaaaaa.json", b"someone else's")
    _ids(monkeypatch, "aaaaaa", "aaaaaa", "bbbbbb")
    result = capture(root, "whoop", "sleep", b"new", META, dt=DAY)
    assert result.outcome == "written"
    assert Path(result.path).name == f"sleep_{MS}_bbbbbb.json"
    assert taken.read_bytes() == b"someone else's"


# @spec CAPTURE-WRITE-007, CAPTURE-WRITE-011
def test_a_sidecar_name_collision_removes_this_attempts_payload_and_retries(
    root: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = put(folder, f"sleep_{MS}_aaaaaa.meta.json", b'{"keep":true}')
    _ids(monkeypatch, "aaaaaa", "bbbbbb")
    result = capture(root, "whoop", "sleep", b"new", META, dt=DAY)
    assert Path(result.path).name == f"sleep_{MS}_bbbbbb.json"
    assert not (folder / f"sleep_{MS}_aaaaaa.json").exists()
    assert taken.read_bytes() == b'{"keep":true}'


# @spec CAPTURE-WRITE-007
def test_five_collisions_fail_the_capture(
    root: Path, folder: Path, monkeypatch: pytest.MonkeyPatch, syscalls: Syscalls
) -> None:
    put(folder, f"sleep_{MS}_aaaaaa.json", b"taken")
    _ids(monkeypatch, "aaaaaa")
    result = capture(root, "whoop", "sleep", b"new", META, dt=DAY)
    assert (result.outcome, result.error_kind) == ("failed", "other")
    assert len(syscalls.of("link")) == 5
    assert tree(root) == [f"whoop/sleep/dt={DAY}/sleep_{MS}_aaaaaa.json"]


# @spec CAPTURE-WRITE-008
def test_a_failed_sidecar_link_rolls_back_the_payload(
    root: Path, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = os.link

    def sidecar_fails(src: str, dst: str, *args: object, **kw: object) -> None:
        if str(dst).endswith(".meta.json"):
            raise OSError(errno.EIO, "Input/output error")
        real_link(src, dst, *args, **kw)

    monkeypatch.setattr(os, "link", sidecar_fails)
    result = capture(root, "whoop", "sleep", b"new", META, dt=DAY)
    assert (result.outcome, result.path) == ("failed", None)
    assert tree(root) == []


# @spec CAPTURE-WRITE-009
def test_a_failed_folder_sync_after_both_links_is_still_written(
    root: Path, folder: Path, monkeypatch: pytest.MonkeyPatch, logs: Logs
) -> None:
    real_fsync, real_link = os.fsync, os.link
    linked: list[str] = []

    def link(src: str, dst: str, *args: object, **kw: object) -> None:
        real_link(src, dst, *args, **kw)
        linked.append(str(dst))

    def fsync(fd: int) -> None:
        if len(linked) == 2:
            raise OSError(errno.EIO, "Input/output error")
        real_fsync(fd)

    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(os, "fsync", fsync)
    result = capture(root, "whoop", "sleep", b"new", META, dt=DAY)
    assert result.outcome == "written" and Path(result.path).is_file()
    [event] = logs.events("capture_folder_sync_failed")
    assert event["level"] == "warning" and "OSError" in event["error"]


# @spec CAPTURE-FAIL-001
@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (errno.ENOSPC, "environment"),
        (errno.EDQUOT, "environment"),
        (errno.EROFS, "environment"),
        (errno.EACCES, "environment"),
        (errno.EPERM, "environment"),
        (errno.EIO, "other"),
    ],
)
def test_environment_failures_are_classified(
    root: Path, monkeypatch: pytest.MonkeyPatch, code: int, kind: str
) -> None:
    def failing(*_args: object, **_kw: object) -> None:
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(os, "link", failing)
    assert capture(root, "whoop", "sleep", b"x", META, dt=DAY).error_kind == kind


# @spec CAPTURE-FAIL-001
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_an_unwritable_root_is_an_environment_failure(root: Path) -> None:
    root.chmod(0o555)
    try:
        result = capture(root, "whoop", "sleep", b"x", META, dt=DAY)
    finally:
        root.chmod(0o755)
    assert (result.outcome, result.error_kind) == ("failed", "environment")


# @spec CAPTURE-FAIL-001
def test_outcomes_other_than_failed_have_no_error_kind(root: Path) -> None:
    assert capture(root, "whoop", "sleep", b"x", META, dt=DAY).error_kind is None
    assert capture(root, "whoop", "sleep", b"x", META, dt=DAY).error_kind is None
    refused = capture(root, "whoop", "sleep", b"x", {**META, "http_status": 500}, dt=DAY)
    assert refused.error_kind is None


# @spec CAPTURE-FAIL-002
def test_an_os_failure_names_its_errno_and_destination_folder(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*_args: object, **_kw: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "link", full)
    failed = capture(root, "whoop", "sleep", b"x", META, dt=DAY)
    assert (failed.errno, failed.folder) == ("ENOSPC", str(root / "whoop" / "sleep" / f"dt={DAY}"))
    monkeypatch.undo()
    written = capture(root, "whoop", "sleep", b"y", META, dt=DAY)
    refused = capture(root, "Whoop", "sleep", b"z", META, dt=DAY)
    assert (written.errno, written.folder, refused.errno, refused.folder) == (
        None,
        None,
        None,
        None,
    )
