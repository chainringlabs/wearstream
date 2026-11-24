"""Low-level file operations for the capture tree: durable folders, temporary files, links."""

from __future__ import annotations

import errno
import os
import secrets
from pathlib import Path

FILE_MODE = 0o644  # filtered by the process umask
FOLDER_MODE = 0o755
ENVIRONMENT_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EACCES, errno.EPERM})


def fsync_folder(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# @spec CAPTURE-WRITE-005
def make_folders(root: Path, folder: Path) -> None:
    """Create the missing folders from `root` down to `folder`, syncing each new one's parent."""
    missing = []
    current = folder
    while current != root and not current.is_dir():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        try:
            os.mkdir(path, FOLDER_MODE)
        except FileExistsError:
            continue
        fsync_folder(path.parent)


def write_temporary(folder: Path, data: bytes) -> Path:
    """Write `data` to a new `.tmp_` file in `folder` and fsync it."""
    path = folder / f".tmp_{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, FILE_MODE)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def remove_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# @spec CAPTURE-FAIL-001
def error_kind(exc: BaseException) -> str:
    """`environment` for failures every later write would repeat, else `other`."""
    if isinstance(exc, OSError) and exc.errno in ENVIRONMENT_ERRNOS:
        return "environment"
    return "other"


# @spec CAPTURE-LOG-002
def describe_error(exc: BaseException) -> str:
    """The exception type and its message, the message cut to 300 characters."""
    return f"{type(exc).__name__}: {str(exc)[:300]}"
