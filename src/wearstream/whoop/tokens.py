"""Keeping a valid access token while never presenting a refresh token twice
(Whoop auth design, *When to refresh* to *When a person is needed*)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx

from wearstream.runner.context import Stopped
from wearstream.state import iso, parse_time
from wearstream.whoop import clock, oauth
from wearstream.whoop.errors import OutOfTime, WhoopAuthError
from wearstream.whoop.files import (
    ClientMissing,
    TokenStore,
    TokenUnreadable,
    errno_name,
    precheck,
)

__all__ = ["TokenManager", "TokenStore", "TokenUnreadable", "WhoopAuthError"]

REFRESH_AFTER = dt.timedelta(minutes=30)
EXPIRY_MARGIN = dt.timedelta(minutes=5)
OFF_SCHEDULE_MINUTES = range(6, 55)  # minutes of the hour other triggers may refresh in
ATTEMPTS, TOKEN_ATTEMPT_SECONDS, MARGIN_SECONDS = 3, 35.0, 60.0
AUTH = (
    "Run `wearstream auth whoop` (for example `docker exec -it <container> wearstream auth whoop`)."
)
REFUSED = {
    "invalid_grant": ("whoop_token_invalid_grant", AUTH),
    "invalid_client": ("whoop_client_rejected", "Check /secrets/whoop/client_id and client_secret "
                       "against the Whoop developer dashboard."),
    "unauthorized_client": ("whoop_client_rejected", "Check /secrets/whoop/client_id and "
                            "client_secret against the Whoop developer dashboard."),
}  # fmt: skip
UNWRITABLE = (
    "Make /secrets/whoop writable by the container user, or free space; WearStream resumes on "
    "its own."
)
GRANT_LOST = " The rotated token could not be saved, so the grant is lost: " + AUTH


class TokenManager:
    """One unit's view of the token: when to refresh, how, and what a failure means."""

    def __init__(
        self, folder: Path, *, trigger: str, signals: Any, log: Any, deadline: float | None = None
    ) -> None:
        self.store, self.trigger, self.signals, self.log = TokenStore(folder), trigger, signals, log
        self.deadline = deadline
        self.cached: str | None = None
        self.refused = False  # a data request was answered 401: a later unit refreshes

    def _needs(self, event: str, action: str, **fields: Any) -> WhoopAuthError:
        self.log.error(event, action=action, **fields)
        return WhoopAuthError(event, needs_person=True, event=event)

    # @spec WHOOP-AUTH-NEED-001, WHOOP-AUTH-NEED-002, WHOOP-AUTH-NEED-003
    def _load(self) -> dict[str, Any]:
        try:
            doc = self.store.read()
        except FileNotFoundError:
            raise self._needs("whoop_credential_missing", AUTH) from None
        except TokenUnreadable:
            raise self._needs(
                "whoop_token_unreadable",
                "Fix the permissions of /secrets/whoop/token.json, or "
                + AUTH[0].lower()
                + AUTH[1:],
            ) from None
        try:
            self.store.client()
        except ClientMissing:
            raise self._needs(
                "whoop_client_credentials_missing",
                "Supply /secrets/whoop/client_id and /secrets/whoop/client_secret.",
            ) from None
        return doc

    # @spec WHOOP-AUTH-REFRESH-001, WHOOP-AUTH-REFRESH-014
    def _decide(self, doc: dict[str, Any]) -> str:
        """`use`, `refresh` or `defer` for this unit's trigger."""
        now = clock.utc_now()
        expires = parse_time(doc.get("expires_at")) or now
        if self.trigger == "schedule":
            refreshed = parse_time(doc.get("refreshed_at"))
            stale = refreshed is None or now - refreshed > REFRESH_AFTER
            return "refresh" if stale or expires - now < EXPIRY_MARGIN else "use"
        if expires > now:
            return "use"
        if doc.get("refresh_failed_at") or now.minute not in OFF_SCHEDULE_MINUTES:
            return "defer"
        return "refresh"

    # @spec WHOOP-AUTH-REFRESH-002, WHOOP-AUTH-REFRESH-015
    def access_token(self) -> str:
        if self.refused:
            raise WhoopAuthError("the access token was refused", event="whoop_token_expired")
        if self.cached:
            return self.cached
        doc = self._load()
        if self._decide(doc) == "use":
            self.cached = doc["access_token"]
            return self.cached
        self._writable()  # before the token lock, whose file may not exist yet
        with self.store.locked():
            doc = self._load()
            decision = self._decide(doc)
            if decision == "use":
                self.cached = doc["access_token"]
                return self.cached
            if decision == "defer":
                self.log.warning(
                    "whoop_refresh_deferred",
                    reason="the token expired outside a scheduled tick; the next tick refreshes it",
                )
                raise WhoopAuthError("refresh deferred", event="whoop_refresh_deferred")
            return self._refresh(doc)

    # @spec WHOOP-AUTH-REFRESH-005, WHOOP-AUTH-NEED-004
    def _writable(self) -> None:
        try:
            precheck(self.store.folder)
        except OSError as exc:
            raise self._needs(
                "whoop_token_folder_unwritable", UNWRITABLE, errno=errno_name(exc)
            ) from None

    def _room(self, seconds: float) -> bool:
        return (
            self.deadline is None or self.deadline - clock.monotonic() - seconds >= MARGIN_SECONDS
        )

    # @spec WHOOP-AUTH-REFRESH-005, WHOOP-AUTH-REFRESH-006, WHOOP-AUTH-REFRESH-007
    # @spec WHOOP-AUTH-EXCH-001, WHOOP-AUTH-EXCH-002, WHOOP-AUTH-EXCH-004, WHOOP-AUTH-REFRESH-003
    # @spec WHOOP-AUTH-LOG-003
    def _refresh(self, doc: dict[str, Any]) -> str:
        client_id, secret = self.store.client()
        form = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": secret,
            "refresh_token": doc["refresh_token"],
            "scope": "offline",
        }
        for attempt in range(1, ATTEMPTS + 1):
            if not self._room(TOKEN_ATTEMPT_SECONDS):
                raise OutOfTime
            self._writable()
            if self.signals.received:
                raise Stopped
            try:
                with self.signals.exchange():
                    response = oauth.post_token(form)
                    return self._handle(response)
            except oauth.CONNECT_ERRORS as exc:
                failure = type(exc).__name__
            except WhoopAuthError:
                raise
            except Exception as exc:
                raise self._uncertain(type(exc).__name__) from None
            if attempt < ATTEMPTS:
                pause = min(2 ** (attempt - 1), 8)
                if not self._room(pause + TOKEN_ATTEMPT_SECONDS):
                    raise OutOfTime
                clock.sleep(pause)
        self.log.warning("whoop_token_refresh_failed", error_type=failure, attempts=ATTEMPTS)
        raise WhoopAuthError(failure, event="whoop_token_refresh_failed")

    # @spec WHOOP-AUTH-REFRESH-016, WHOOP-AUTH-NEED-007, WHOOP-AUTH-LOG-001, WHOOP-AUTH-LOG-002
    def _uncertain(self, error_type: str, **fields: Any) -> WhoopAuthError:
        """Whoop may have received the refresh token: mark it, so only a scheduled unit retries."""
        try:
            self.store.write({"refresh_failed_at": iso(clock.utc_now())})
        except OSError:
            pass
        self.log.warning("whoop_token_refresh_failed", error_type=error_type, **fields)
        return WhoopAuthError(error_type, event="whoop_token_refresh_failed")

    # @spec WHOOP-AUTH-NEED-005, WHOOP-AUTH-LOG-004
    def _refused(self, response: httpx.Response) -> WhoopAuthError:
        code = oauth.oauth_error(response)
        if code in REFUSED:
            event, action = REFUSED[code]
            return self._needs(event, action, status_code=response.status_code)
        return self._needs(
            "whoop_token_refused", AUTH, status_code=response.status_code, oauth_error=code
        )

    # @spec WHOOP-AUTH-REFRESH-009, WHOOP-AUTH-REFRESH-010, WHOOP-AUTH-REFRESH-011
    # @spec WHOOP-AUTH-REFRESH-012
    def _handle(self, response: httpx.Response) -> str:
        if response.status_code in (400, 401):
            raise self._refused(response)
        if response.status_code != 200:
            raise self._uncertain("HTTPStatusError", status_code=response.status_code)
        body = oauth.json_object(response) or {}
        access, expires_in, rotated = (
            body.get("access_token"),
            body.get("expires_in"),
            body.get("refresh_token"),
        )
        now = clock.utc_now()
        valid_expiry = isinstance(expires_in, int) and not isinstance(expires_in, bool)
        if not (isinstance(access, str) and access and valid_expiry and expires_in > 0):
            if isinstance(rotated, str) and rotated:
                self._save({"refresh_token": rotated, "expires_at": iso(now)})
            self.log.warning("whoop_token_refresh_failed", reason="malformed token response")
            raise WhoopAuthError("malformed token response", event="whoop_token_refresh_failed")
        fields = {
            "access_token": access,
            "expires_at": iso(now + dt.timedelta(seconds=expires_in)),
            "refreshed_at": iso(now),
            "refresh_failed_at": None,
        }
        if isinstance(rotated, str) and rotated:
            fields["refresh_token"] = rotated
        else:
            self.log.warning("whoop_token_no_rotation", reason="Whoop kept the refresh token")
        self._save(fields)
        self.cached = access
        return access

    # @spec WHOOP-AUTH-NEED-004
    def _save(self, fields: dict[str, Any]) -> None:
        try:
            self.store.write(fields)
        except OSError as exc:
            raise self._needs(
                "whoop_token_folder_unwritable", UNWRITABLE + GRANT_LOST, errno=errno_name(exc)
            ) from None

    # @spec WHOOP-AUTH-REFRESH-013
    def expire(self) -> None:
        """A data request was answered 401: a later unit refreshes under the usual rules."""
        self.cached, self.refused = None, True
        self.log.warning(
            "whoop_token_expired", reason="Whoop refused the access token; a later unit refreshes"
        )
        try:
            with self.store.locked():
                self.store.write({"expires_at": iso(clock.utc_now())})
        except OSError:
            pass

    # @spec WHOOP-AUTH-NEED-006
    def scope_missing(self) -> WhoopAuthError:
        return self._needs(
            "whoop_scope_missing",
            "Run `wearstream auth whoop` and grant every read: scope Whoop offers.",
        )
