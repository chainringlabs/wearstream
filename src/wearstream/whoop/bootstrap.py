"""`wearstream auth whoop`: the first token, by pasting back the redirected address
(Whoop auth design, *Getting the first token*)."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import secrets
import signal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from wearstream import jobs, log
from wearstream.runner.context import Signals, Stopped
from wearstream.settings import ensure_valid, load, paths
from wearstream.state import iso, release, try_lock, wait_lock
from wearstream.whoop import clock, http, oauth
from wearstream.whoop.confirm import REQUIRED_SCOPES, confirm
from wearstream.whoop.files import ClientMissing, TokenStore, bounded_write

EXTRA_WAIT_SECONDS = 120.0  # beyond the longest capture time limit, so a running unit finishes
PASTE_HELP = (
    "Paste the full address your browser was sent to after you approved (it starts with your "
    "redirect URI and holds code= and state=). If the browser never got there, the redirect URI "
    "configured in WearStream doesn't match the one registered with Whoop."
)


def _challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


# @spec WHOOP-AUTH-BOOT-002
def authorization_url(client_id: str, redirect_uri: str, state: str, verifier: str) -> str:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(REQUIRED_SCOPES),
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{http.AUTHORIZE_URL}?{urlencode(query)}"


# @spec WHOOP-AUTH-BOOT-003, WHOOP-AUTH-BOOT-004
def parse_paste(text: str, expected_state: str) -> tuple[str | None, str | None]:
    """The authorization code, or the problem with what was pasted."""
    cleaned = text.strip().strip("'\"").strip()
    parts = urlsplit(cleaned)
    if not parts.scheme or not parts.netloc or not parts.query:
        return None, "That isn't the address the browser was sent to. " + PASTE_HELP
    query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    if "error" in query:
        return None, f"Whoop reported {query['error']}: the authorization was declined or failed."
    if not query.get("code"):
        return None, "The address has no code parameter. " + PASTE_HELP
    if not query.get("state"):
        return None, "The address has no state parameter. " + PASTE_HELP
    if query["state"] != expected_state:
        return None, "The state in the address doesn't match this run; use this run's link."
    return query["code"], None


def _lock_source(values: dict[str, Any], lock: Path) -> int | None:
    fd = try_lock(lock)
    if fd is not None:
        return fd
    print("Waiting for a running Whoop unit to finish before saving the new token...")
    longest = max(
        values[jobs.setting("whoop", job, "TIME_LIMIT")] for job in ("range", "snapshots")
    )
    return wait_lock(lock, longest * jobs.SECONDS_PER_MINUTE + EXTRA_WAIT_SECONDS)


# @spec WHOOP-AUTH-BOOT-007, WHOOP-AUTH-EXCH-001
def _exchange(store: TokenStore, form: dict[str, str], signals: Signals) -> dict[str, Any] | str:
    """The code exchange and the staging write, as one credential exchange under the token lock;
    the staged token, or why it failed."""
    with store.locked():
        with signals.exchange():
            try:
                response = oauth.post_token(form)
            except Exception as exc:
                return f"the exchange with Whoop failed ({type(exc).__name__})"
            body = oauth.json_object(response) if response.status_code == 200 else None
            if (
                not body
                or not body.get("access_token")
                or not isinstance(body.get("expires_in"), int)
            ):
                code = oauth.oauth_error(response)
                return (
                    f"Whoop refused the exchange (HTTP {response.status_code}, {code or 'no code'})"
                )
            now = clock.utc_now()
            scope = body.get("scope")
            staged = {
                "access_token": body["access_token"],
                "refresh_token": body.get("refresh_token"),
                "token_type": body.get("token_type", "bearer"),
                "expires_at": iso(now + dt.timedelta(seconds=body["expires_in"])),
                "scopes": scope.split() if isinstance(scope, str) else None,
            }
            staged = {k: v for k, v in staged.items() if v is not None}
            bounded_write(store.staging, staged)
            return staged


def _authorize(values: dict[str, Any], resolved: Any, new_account: bool) -> int:
    store = TokenStore(resolved.secrets_path("whoop"))
    try:
        client_id, secret = store.client()
    except ClientMissing as missing:
        print(f"/secrets/whoop/{missing} is missing or empty: supply client_id and client_secret.")
        return 2
    redirect, state, verifier = (
        values["WEARSTREAM_WHOOP_REDIRECT_URI"],
        secrets.token_urlsafe(24),
        secrets.token_urlsafe(64),
    )
    print("Open this address in a browser, sign in to Whoop and approve WearStream:\n")
    print(authorization_url(client_id, redirect, state, verifier) + "\n")
    print("The browser then goes to an address that doesn't load; that's expected.")
    code, problem = parse_paste(input("Paste that address here, promptly: "), state)
    if problem or code is None:
        print(problem)
        return 2
    fd = _lock_source(values, resolved.state_path("locks", "whoop.lock"))
    if fd is None:
        print("A Whoop unit still holds its lock; nothing was exchanged. Try again shortly.")
        return 5
    try:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": client_id,
            "client_secret": secret,
            "code_verifier": verifier,
        }
        return confirm(store, _exchange(store, form, _signals()), new_account)
    finally:
        release(fd)


def _signals() -> Signals:
    signals = Signals()
    signals.install()
    return signals


# @spec WHOOP-AUTH-BOOT-001, WHOOP-AUTH-BOOT-005, WHOOP-AUTH-BOOT-006, WHOOP-AUTH-BOOT-012
def auth_whoop(new_account: bool) -> int:
    """Never listens on a port; holds no lock while the operator is in the browser."""
    loaded = load(os.environ, "auth", "whoop")
    ensure_valid(loaded, "auth")
    values = loaded.values
    log.configure(fmt=values["WEARSTREAM_LOG_FORMAT"], level=values["WEARSTREAM_LOG_LEVEL"])
    resolved = paths(values)
    if not resolved.state_path("locks").is_dir():
        print(
            "/state/locks doesn't exist: mount the same /state the daemon uses, so a running "
            "Whoop unit can be excluded while the new token is saved."
        )
        return 2
    saved = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        return _authorize(values, resolved, new_account)
    except Stopped:
        print("Stopped. /secrets/whoop/token.json was not changed; run the command again.")
        return 1
    finally:
        signal.signal(signal.SIGTERM, saved[0])
        signal.signal(signal.SIGINT, saved[1])
