"""The files under `/secrets/whoop`: client credentials, the token, its lock, and the
credential fingerprint (Whoop auth design, *Storage*)."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REQUIRED = ("access_token", "refresh_token", "expires_at")
WRITE_LIMIT_SECONDS = 5.0
_PROCESS_LOCK = threading.Lock()  # serializes refreshes inside one process


class TokenUnreadable(Exception):
    """The token file exists but can't be read, parsed, or lacks a required key."""


class ClientMissing(Exception):
    """A client id or secret file is missing or empty."""


def _fsync_folder(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, doc: dict[str, Any]) -> None:
    """A private temporary file in the same folder, fsynced, renamed into place, folder fsynced."""
    temporary = path.parent / f".{path.name}.{secrets.token_hex(4)}.tmp"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, json.dumps(doc, indent=2).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    _fsync_folder(path.parent)


# @spec WHOOP-AUTH-EXCH-003
def bounded_write(path: Path, doc: dict[str, Any]) -> None:
    """`atomic_write`, given at most 5 seconds; a write that takes longer counts as failed."""
    failure: list[OSError] = []

    def write() -> None:
        try:
            atomic_write(path, doc)
        except OSError as exc:
            failure.append(exc)

    worker = threading.Thread(target=write, name="token-write", daemon=True)
    worker.start()
    worker.join(WRITE_LIMIT_SECONDS)
    if worker.is_alive():
        raise OSError(errno.ETIMEDOUT, "the token write took too long")
    if failure:
        raise failure[0]


# @spec WHOOP-AUTH-REFRESH-005
def precheck(folder: Path) -> None:
    """Create, fsync and remove a file in the folder; raises the OSError if it can't."""
    probe = folder / f".write-check.{secrets.token_hex(4)}"
    fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, b"x")
        os.fsync(fd)
    finally:
        os.close(fd)
        probe.unlink(missing_ok=True)


def errno_name(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, "EIO")


class TokenStore:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.path = self.folder / "token.json"
        self.staging = self.folder / "token.json.new"
        self.lock_path = self.folder / "token.json.lock"

    # @spec WHOOP-AUTH-STORE-001
    def client(self) -> tuple[str, str]:
        values = []
        for name in ("client_id", "client_secret"):
            try:
                value = (self.folder / name).read_text().strip()
            except OSError, UnicodeDecodeError:
                value = ""
            if not value:
                raise ClientMissing(name)
            values.append(value)
        return values[0], values[1]

    # @spec WHOOP-AUTH-STORE-003
    def read(self) -> dict[str, Any]:
        """The token; FileNotFoundError when there is none, TokenUnreadable when it's unusable."""
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        try:
            doc = json.loads(self.path.read_bytes())
        except (OSError, ValueError) as exc:
            raise TokenUnreadable(type(exc).__name__) from None
        if not isinstance(doc, dict) or any(not doc.get(key) for key in REQUIRED):
            raise TokenUnreadable("missing a required key")
        return doc

    # @spec WHOOP-AUTH-STORE-002
    def write(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Merge `fields` into the stored token (a None value removes the key) and write it whole,
        within the write limit."""
        try:
            previous = json.loads(self.path.read_bytes())
        except OSError, ValueError:
            previous = {}
        doc = {**(previous if isinstance(previous, dict) else {}), **fields}
        doc = {key: value for key, value in doc.items() if value is not None}
        bounded_write(self.path, doc)
        return doc

    @contextmanager
    def locked(self) -> Iterator[None]:
        """The in-process lock, then the token lock (an exclusive `flock` on `token.json.lock`)."""
        with _PROCESS_LOCK:
            fd = os.open(
                self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600
            )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)


def _can_create(folder: Path) -> bool:
    try:
        precheck(folder)
        return True
    except OSError:
        return False


# @spec WHOOP-AUTH-FP-001
def fingerprint(folder: Path) -> str:
    """File metadata only, never contents: a new token, a fixed client file or a fixed folder
    changes it; a dead token left in place doesn't."""
    digest = hashlib.sha256()
    for name in ("token.json", "client_id", "client_secret"):
        try:
            st = os.stat(folder / name)
            line = f"{name}:1:{st.st_size}:{st.st_mtime_ns}:{st.st_ino}:{st.st_mode}:{st.st_uid}"
        except OSError:
            line = f"{name}:0"
        digest.update(line.encode() + b"\n")
    try:
        st = os.stat(folder)
        digest.update(f"folder:{st.st_mode}:{st.st_uid}\n".encode())
    except OSError:
        digest.update(b"folder:missing\n")
    digest.update(f"writable:{_can_create(folder)}".encode())
    return digest.hexdigest()
