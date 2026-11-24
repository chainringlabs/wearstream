"""The checks after a code exchange, then the rename into place
(Whoop auth design, *Getting the first token*, steps 6 to 9)."""

from __future__ import annotations

import os
from typing import Any

from wearstream import log
from wearstream.state import iso
from wearstream.whoop import clock, http
from wearstream.whoop.files import TokenStore, TokenUnreadable, bounded_write

REQUIRED_SCOPES = ("read:sleep", "read:workout", "read:recovery", "read:cycles", "read:profile",
                   "read:body_measurement", "offline")  # fmt: skip


def _refuse(store: TokenStore, message: str, code: int) -> int:
    store.staging.unlink(missing_ok=True)
    print(message + " /secrets/whoop/token.json was not changed.")
    return code


# @spec WHOOP-AUTH-BOOT-008
def _missing(staged: dict[str, Any]) -> list[str]:
    missing = [] if staged.get("refresh_token") else ["a refresh token (the offline scope)"]
    granted = staged.get("scopes")
    if isinstance(granted, list):
        missing += [scope for scope in REQUIRED_SCOPES if scope not in granted]
    return missing


# @spec WHOOP-AUTH-BOOT-009
def _profile_user(access_token: str) -> str | None:
    try:
        with http.data_client() as client:
            response = client.get(
                "/developer/v2/user/profile/basic",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        user = response.json().get("user_id") if response.status_code == 200 else None
    except Exception:
        return None
    return str(user) if user not in (None, "") else None


def _existing_user(store: TokenStore) -> str | None:
    try:
        user = store.read().get("user_id")
    except FileNotFoundError, TokenUnreadable:
        return None
    return str(user) if user else None


# @spec WHOOP-AUTH-BOOT-008, WHOOP-AUTH-BOOT-009, WHOOP-AUTH-BOOT-010, WHOOP-AUTH-BOOT-011
def confirm(store: TokenStore, staged: dict[str, Any] | str, new_account: bool) -> int:
    """Check the staged token, then rename it over `token.json` under the token lock."""
    if isinstance(staged, str):
        return _refuse(store, f"Authorization failed: {staged}.", 1)
    missing = _missing(staged)
    if missing:
        return _refuse(store, "Whoop's grant is missing " + ", ".join(missing) + ".", 1)
    user = _profile_user(staged["access_token"])
    if user is None:
        return _refuse(store, "The Whoop profile couldn't be fetched to confirm the account.", 1)
    previous = _existing_user(store)
    if previous and previous != user and not new_account:
        message = (
            f"This grant is for Whoop user {user}, but the current token is for user {previous}. "
            "Run again with --new-account to switch accounts."
        )
        return _refuse(store, message, 2)
    with store.locked():
        bounded_write(
            store.staging, {**staged, "user_id": user, "refreshed_at": iso(clock.utc_now())}
        )
        os.replace(store.staging, store.path)
        fd = os.open(store.folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    scopes = " ".join(staged.get("scopes") or REQUIRED_SCOPES)
    log.get_logger("whoop").info("whoop_authorized", user_id=user, scopes=scopes)
    print(f"Authorized Whoop user {user} with scopes: {scopes}")
    return 0
