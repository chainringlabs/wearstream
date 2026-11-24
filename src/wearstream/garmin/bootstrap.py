"""`wearstream auth garmin`: sign in in memory, check, then write the session
(Garmin auth design, *Getting a session*)."""

from __future__ import annotations

import getpass
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from wearstream import jobs, log
from wearstream.garmin import library
from wearstream.garmin.store import read_credentials, token_file
from wearstream.settings import ensure_valid, load, paths
from wearstream.state import release, try_lock, wait_lock

MFA_TIMEOUT_SECONDS = 300.0
EXTRA_WAIT_SECONDS = 120.0  # beyond the longest capture time limit, so a running unit finishes


class _Mfa:
    """Asks for the code, giving up after five minutes without an answer."""

    def __init__(self) -> None:
        self.asked = False

    # @spec GARMIN-AUTH-BOOT-004
    def prompt(self) -> str:
        self.asked = True
        answer: list[str] = []
        reader = threading.Thread(
            target=lambda: answer.append(input("Garmin sent a sign-in code; enter it: ").strip()),
            daemon=True,
        )
        reader.start()
        reader.join(MFA_TIMEOUT_SECONDS)
        if not answer:
            raise TimeoutError("no MFA code was entered in time")
        return answer[0]


def _write_private(path: Path, text: str) -> None:
    """A temporary file created 0600, fsynced, renamed into place, the folder fsynced."""
    temporary = path.parent / f".{path.name}.{secrets.token_hex(4)}.tmp"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, text.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    folder = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def _previous_profile(folder: Path) -> str | None:
    try:
        found = json.loads((folder / "account.json").read_text()).get("profile_id")
    except OSError, ValueError, AttributeError:
        return None
    return str(found) if found is not None else None


# @spec GARMIN-AUTH-BOOT-002, GARMIN-AUTH-BOOT-003, GARMIN-AUTH-BOOT-009
def _sign_in(values: dict[str, Any], folder: Path) -> tuple[Any, int | None]:
    """A client signed in with no token store, or the exit code for why not."""
    email, password = read_credentials(folder)
    email = email or input("Garmin Connect email: ").strip()
    password = password or getpass.getpass("Garmin Connect password (not shown): ")
    mfa = _Mfa()
    client = library.make_client(values, email, password, prompt_mfa=mfa.prompt)
    try:
        client.login()
    except Exception as exc:
        if mfa.asked:
            print("The sign-in code was wrong or not entered in time. Nothing was written.")
            return None, 2
        print(f"Garmin sign-in failed ({type(exc).__name__}). Nothing was written.")
        return None, 1
    return client, None


# @spec GARMIN-AUTH-BOOT-005, GARMIN-AUTH-BOOT-006, GARMIN-AUTH-BOOT-007, GARMIN-AUTH-BOOT-008
def _authorize(values: dict[str, Any], resolved: Any, new_account: bool) -> int:
    folder = resolved.secrets_path("garmin")
    client, code = _sign_in(values, folder)
    if client is None:
        return code or 1
    session = client.client.dumps()
    parsed = json.loads(session)
    if not (parsed.get("di_token") and parsed.get("di_refresh_token")):
        print("Garmin's session can't be resumed later (no refresh token). Nothing was written.")
        return 1
    profile, previous = str(client.profile_id), _previous_profile(folder)
    if previous and previous != profile and not new_account:
        print(
            f"This sign-in is for Garmin profile {profile}, but the saved session is for profile "
            f"{previous}. Run again with --new-account to switch accounts. Nothing was written."
        )
        return 2
    path = token_file(folder)
    if path is None:
        print("/secrets/garmin can't be used (a symlink in its path?). Nothing was written.")
        return 1
    lock = resolved.state_path("locks", "garmin.lock")
    fd = try_lock(lock)
    if fd is None:
        print("Waiting for a running Garmin unit to finish before saving the session...")
        longest = max(
            values[jobs.setting("garmin", j, "TIME_LIMIT")] for j in ("daily", "reference")
        )
        fd = wait_lock(lock, longest * jobs.SECONDS_PER_MINUTE + EXTRA_WAIT_SECONDS)
    if fd is None:
        print("A Garmin unit still holds its lock; nothing was written. Try again shortly.")
        return 5
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private(path, session)
        _write_private(folder / "account.json", json.dumps({"profile_id": client.profile_id}))
    finally:
        release(fd)
    log.get_logger("garmin").info("garmin_authorized", profile_id=profile)
    print(f"Signed in to Garmin Connect as {client.display_name} (profile {profile}).")
    return 0


# @spec GARMIN-AUTH-BOOT-001
def auth_garmin(new_account: bool) -> int:
    loaded = load(os.environ, "auth", "garmin")
    ensure_valid(loaded, "auth")
    values = loaded.values
    log.configure(fmt=values["WEARSTREAM_LOG_FORMAT"], level=values["WEARSTREAM_LOG_LEVEL"])
    library.prepare(values, log.get_logger("garmin"))
    resolved = paths(values)
    if not resolved.state_path("locks").is_dir():
        print(
            "/state/locks doesn't exist: mount the same /state the daemon uses, so a running "
            "Garmin unit can be excluded while the session is saved."
        )
        return 2
    return _authorize(values, resolved, new_account)
