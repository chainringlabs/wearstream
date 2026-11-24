"""Checking, before any capture, that a folder supports the atomic write sequence."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from wearstream.capture import fsio


@dataclass(frozen=True)
class SelfTestResult:
    ok: bool
    step: str | None = None
    folder: Path | None = None
    error: str | None = None


def _test_folder(folder: Path) -> SelfTestResult | None:
    name = f".wearstream-self-test-{secrets.token_hex(4)}"
    first, second = folder / name, folder / f"{name}-link"
    created: list[Path] = []
    step = "create"
    try:
        fd = os.open(first, os.O_WRONLY | os.O_CREAT | os.O_EXCL, fsio.FILE_MODE)
        created.append(first)
        try:
            os.write(fd, b"wearstream self-test\n")
            step = "fsync"
            os.fsync(fd)
        finally:
            os.close(fd)
        step = "link"
        os.link(first, second)
        created.append(second)
        step = "fsync_folder"
        fsio.fsync_folder(folder)
        step = "remove"
        while created:
            os.unlink(created[-1])
            created.pop()
        return None
    except OSError as exc:
        return SelfTestResult(False, step, folder, fsio.describe_error(exc))
    finally:
        for path in created:
            fsio.remove_quietly(path)


# @spec CAPTURE-TEST-001, CAPTURE-TEST-002
def self_test(root: Path | str, folder: Path | str | None = None) -> SelfTestResult:
    """Create, sync, hard-link, sync the folder and remove, in `root` and then in `folder`."""
    for target in [Path(root)] + ([Path(folder)] if folder is not None else []):
        failure = _test_folder(target)
        if failure is not None:
            return failure
    return SelfTestResult(True)
