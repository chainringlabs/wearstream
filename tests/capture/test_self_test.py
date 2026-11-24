"""Checking the data root supports atomic writes before capturing (CAPTURE-TEST)."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest
from capture_helpers import Syscalls

from wearstream.capture import self_test


def _leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


# @spec CAPTURE-TEST-001
def test_the_root_passes_and_is_left_clean(root: Path, syscalls: Syscalls) -> None:
    result = self_test(root)
    assert result.ok and result.step is None and result.error is None
    assert _leftovers(root) == []
    [(_, src, dst)] = syscalls.of("link")
    assert Path(src).name.startswith(".") and Path(dst).name.startswith(".")
    assert Path(src).parent == root == Path(dst).parent
    synced = [call[1] for call in syscalls.of("fsync")]
    assert src in synced and str(root) in synced


# @spec CAPTURE-TEST-001
def test_a_folder_inside_the_root_is_tested_too(root: Path, syscalls: Syscalls) -> None:
    folder = root / "whoop" / "sleep"
    folder.mkdir(parents=True)
    assert self_test(root, folder).ok
    linked_in = {str(Path(call[2]).parent) for call in syscalls.of("link")}
    assert linked_in == {str(root), str(folder)}
    assert _leftovers(folder) == []


# @spec CAPTURE-TEST-002
def test_a_failing_step_is_named_with_its_folder_and_error(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_links(*_args: object, **_kw: object) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    result = self_test(root)
    assert not result.ok
    assert (result.step, result.folder) == ("link", root)
    assert "Operation not permitted" in result.error
    assert _leftovers(root) == []


# @spec CAPTURE-TEST-002
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_an_unwritable_subfolder_is_reported_as_the_failing_folder(root: Path) -> None:
    folder = root / "garmin"
    folder.mkdir()
    folder.chmod(0o555)
    try:
        result = self_test(root, folder)
    finally:
        folder.chmod(0o755)
    assert not result.ok
    assert (result.step, result.folder) == ("create", folder)
    assert _leftovers(root) == ["garmin"] and _leftovers(folder) == []
