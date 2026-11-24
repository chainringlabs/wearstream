"""Kernel locks (`flock`) on files under `/state/locks`, and the process's pid namespace."""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path


def try_lock(path: Path) -> int | None:
    """Take a non-blocking exclusive lock; the open file descriptor, or None if it is held."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def wait_lock(path: Path, seconds: float) -> int | None:
    """Take an exclusive lock, waiting up to `seconds`; the descriptor, or None on giving up."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o644)
    deadline = time.monotonic() + seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.05)


def release(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)


def pid_namespace() -> str | None:
    """This process's pid namespace (Linux), so the daemon signals only runs in its container."""
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return None
