"""The token store and credential files: where they are, whether they work, the fingerprint and
the health check (Garmin auth design, *Storage*, *Making a broken store loud*)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from wearstream.garmin import library

SESSION_FIELDS = ("di_token", "di_refresh_token", "di_client_id")
DEFAULT_FILE = "garmin_tokens.json"
is_mount = os.path.ismount  # tests say which folders are mount points
ACTIONS = {
    "path_rejected": "Mount /secrets/garmin directly, with no symlinks in its path.",
    "not_writable": "Make /secrets/garmin writable by the container user.",
    "parent_not_writable": "Create /secrets/garmin, writable by the container user.",
    "off_volume": "Mount a host folder at /secrets/garmin so the session survives the container.",
    "missing": "Run `wearstream auth garmin` (for example `docker exec -it <container> "
    "wearstream auth garmin`).",
}  # fmt: skip
ACTIONS["unreadable"] = ACTIONS["not_json"] = ACTIONS["missing_session_fields"] = ACTIONS["missing"]


# @spec GARMIN-AUTH-STORE-001
def read_credentials(folder: Path) -> tuple[str | None, str | None]:
    """The optional email and password files, one trailing newline dropped."""
    found: list[str | None] = []
    for name in ("email", "password"):
        try:
            value = (folder / name).read_text()
        except OSError, UnicodeDecodeError:
            found.append(None)
            continue
        value = value[:-1] if value.endswith("\n") else value
        found.append(value or None)
    return found[0], found[1]


# @spec GARMIN-AUTH-STORE-003
def token_file(folder: Path) -> Path | None:
    """The library's own token path for the folder, or None when it refuses the path."""
    try:
        return library.token_file_path(str(folder))
    except ValueError:
        return None


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK | os.X_OK)


# @spec GARMIN-AUTH-GUARD-001
def store_problem(folder: Path) -> str | None:
    """Why the store can't keep a session across units, or None."""
    path = token_file(folder)
    if path is None:
        return "path_rejected"
    directory = path.parent
    if directory.exists():
        if not (directory.is_dir() and _writable(directory)):
            return "not_writable"
    else:
        parent = next(p for p in directory.parents if p.exists())
        if not _writable(parent):
            return "parent_not_writable"
    if not any(is_mount(p) for p in (directory, *directory.parents) if p != Path("/")):
        return "off_volume"
    return None


# @spec GARMIN-AUTH-GUARD-002
def session_saved(folder: Path, session: str, since_ns: int) -> bool:
    """The file holds a usable session that is this client's, or was written since `since_ns`.
    Contents are compared in memory and never logged."""
    path = token_file(folder)
    try:
        stored = json.loads(path.read_text()) if path else None
        current = json.loads(session)
        written = path.stat().st_mtime_ns if path else 0
    except OSError, ValueError:
        return False
    if not isinstance(stored, dict) or not (
        stored.get("di_token") and stored.get("di_refresh_token")
    ):
        return False
    return stored == current or written >= since_ns


def _metadata(path: Path) -> str:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return f"{path.name}:0"
    return f"{path.name}:1:{st.st_size}:{st.st_mtime_ns}:{st.st_ino}:{st.st_mode}:{st.st_uid}"


def _can_create(folder: Path) -> bool:
    probe = folder / f".write-check.{secrets.token_hex(4)}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        probe.unlink()
        return True
    except OSError:
        return False


# @spec GARMIN-AUTH-FP-001
def fingerprint(folder: Path) -> str:
    """File metadata only, never contents; the literal default path when the library refuses
    the folder's path."""
    token = token_file(folder) or folder / DEFAULT_FILE
    lines = [_metadata(token), _metadata(folder / "email"), _metadata(folder / "password")]
    try:
        st = os.stat(folder, follow_symlinks=False)
        lines.append(f"folder:{st.st_mode}:{st.st_uid}")
    except OSError:
        lines.append("folder:missing")
    lines.append(f"writable:{_can_create(folder)}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def token_file_problem(folder: Path) -> str | None:
    """Why the token file can't be resumed from: missing, unreadable, not JSON, or fields."""
    path = token_file(folder) or folder / DEFAULT_FILE
    try:
        doc = json.loads(path.read_text())
    except FileNotFoundError:
        return "missing"
    except OSError, UnicodeDecodeError:
        return "unreadable"
    except ValueError:
        return "not_json"
    if not isinstance(doc, dict) or not all(doc.get(field) for field in SESSION_FIELDS):
        return "missing_session_fields"
    return None


def display_path(folder: Path) -> str:
    return str(token_file(folder) or folder / DEFAULT_FILE)


def problem_fields(folder: Path, reason: str) -> dict[str, Any]:
    return {"path": display_path(folder), "reason": reason, "action": ACTIONS[reason]}
