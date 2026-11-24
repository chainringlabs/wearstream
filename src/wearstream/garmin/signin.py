"""A unit's sign-in: resume without credentials, a rare password sign-in, and what a failure
means (Garmin auth design, *Signing in for a unit* to *Session refresh*)."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterator
from typing import Any

from wearstream.garmin import clock, library
from wearstream.garmin.store import ACTIONS, read_credentials, token_file_problem
from wearstream.runner.context import Stopped
from wearstream.state import StateStore, iso, parse_time

SIGNIN_LOCK_WAIT = 10.0
PASSWORD_SIGNIN_EVERY = dt.timedelta(hours=1)
RESUME_ATTEMPTS = 3
REFUSAL_MARKERS = (
    "invalid username or password",
    "widget authentication failed",
    "401 unauthorized",
)
AUTH_ACTION = ACTIONS["missing"]


class SigninFailed(Exception):
    """`kind` is `needs_person`, `rate_limited` or `failed`."""

    def __init__(self, kind: str, retry_after: int | None = None) -> None:
        super().__init__(kind)
        self.kind, self.retry_after = kind, retry_after


class Watch:
    """What a sign-in learned: whether Garmin asked for MFA, or answered a refresh with 429."""

    def __init__(self, signals: Any) -> None:
        self.signals, self.mfa_asked, self.saw_429 = signals, False, False

    # @spec GARMIN-AUTH-SIGNIN-002
    def mfa(self) -> str:
        self.mfa_asked = True
        raise RuntimeError("MFA required")

    # @spec GARMIN-AUTH-EXCH-001, GARMIN-AUTH-EXCH-002
    def attach(self, client: Any) -> Any:
        """Make each session refresh a credential exchange, and note a 429 the library swallows."""
        inner = client.client
        refresh, refresh_token = inner._refresh_session, inner._refresh_di_token

        def watched_token() -> Any:
            try:
                return refresh_token()
            except Exception as exc:
                self.saw_429 = self.saw_429 or any(code in str(exc) for code in ("429", "427"))
                raise

        def exchange() -> Any:
            if self.signals.received:
                raise Stopped
            with self.signals.exchange():
                return refresh()

        inner._refresh_di_token, inner._refresh_session = watched_token, exchange
        return client


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        yield current
        current = current.__cause__ or current.__context__


def rate_limited(exc: BaseException) -> bool:
    for item in _chain(exc):
        if isinstance(item, library.GarminConnectTooManyRequestsError):
            return True
        if getattr(getattr(item, "response", None), "status_code", None) in (429, 427):
            return True
    return False


# @spec GARMIN-AUTH-CLASS-001, GARMIN-AUTH-CLASS-002, GARMIN-AUTH-CLASS-004, GARMIN-AUTH-CLASS-005
# @spec GARMIN-AUTH-SIGNIN-007, GARMIN-AUTH-LOG-002
def classify(exc: Exception, step: str, watch: Watch, log: Any) -> SigninFailed:
    """A failed sign-in step; a pending stop ends the unit unclassified."""
    if watch.signals.received:
        raise Stopped
    if watch.mfa_asked:
        log.error("garmin_mfa_required", action=AUTH_ACTION)
        return SigninFailed("needs_person")
    if watch.saw_429 or rate_limited(exc):
        log.warning("garmin_rate_limited", step=step, error_type=type(exc).__name__)
        return SigninFailed("rate_limited")
    refused = isinstance(exc, library.GarminConnectAuthenticationError) and any(
        marker in str(exc).lower() for marker in REFUSAL_MARKERS
    )
    if step == "password" and refused:
        log.error(
            "garmin_signin_refused",
            action="Check /secrets/garmin/email and password, then run `wearstream auth garmin`.",
        )
        return SigninFailed("needs_person")
    log.warning("garmin_signin_failed", step=step, error_type=type(exc).__name__)
    return SigninFailed("failed")


# @spec GARMIN-AUTH-SIGNIN-001, GARMIN-AUTH-SIGNIN-004, GARMIN-AUTH-SIGNIN-006
def _resume(ctx: Any, watch: Watch) -> Any | None:
    """A client resumed from the store, or None when the store holds no usable session."""
    folder = ctx.paths.secrets_path("garmin")
    for attempt in range(1, RESUME_ATTEMPTS + 1):
        client = watch.attach(library.make_client(ctx.values, prompt_mfa=watch.mfa))
        try:
            client.login(tokenstore=str(folder))
            return client
        except Exception as exc:
            if watch.signals.received or watch.mfa_asked or watch.saw_429 or rate_limited(exc):
                raise classify(exc, "resume", watch, ctx.log) from None
            if token_file_problem(folder) or isinstance(
                exc, library.GarminConnectAuthenticationError
            ):
                return None
            if attempt == RESUME_ATTEMPTS:
                raise classify(exc, "resume", watch, ctx.log) from None
            ctx.log.info("garmin_resume_retry", attempt=attempt, error_type=type(exc).__name__)
        clock.sleep(min(2.0 * 2 ** (attempt - 1), 15.0))
    return None  # unreachable


# @spec GARMIN-AUTH-SIGNIN-003, GARMIN-AUTH-SIGNIN-008
def claim_password_signin(ctx: Any) -> None:
    """Record this password sign-in, unless one happened less than an hour ago."""
    store = StateStore(ctx.paths.state)
    store.lock_wait = SIGNIN_LOCK_WAIT
    wait: list[int] = []

    def change(doc: dict[str, Any] | None) -> dict[str, Any]:
        doc, now = dict(doc or {}), clock.utc_now()
        last = parse_time(doc.get("last_password_signin"))
        if last is not None and now - last < PASSWORD_SIGNIN_EVERY:
            remaining = (PASSWORD_SIGNIN_EVERY - (now - last)).total_seconds()
            wait.append(min(3600, max(1, math.ceil(remaining))))
            return doc
        return {**doc, "last_password_signin": iso(now)}

    try:
        recorded = store.update("garmin/signin.json", change)
    except OSError:
        recorded = None
    if recorded is None:
        ctx.log.warning("garmin_signin_failed", reason="could not record the password sign-in")
        raise SigninFailed("failed")
    if wait:
        ctx.log.warning("garmin_signin_deferred", retry_after=wait[0])
        raise SigninFailed("rate_limited", retry_after=wait[0])


# @spec GARMIN-AUTH-CLASS-003
def sign_in(ctx: Any) -> Any:
    """A signed-in client for this unit, or SigninFailed."""
    watch = Watch(ctx.signals)
    client = _resume(ctx, watch)
    if client is not None:
        return client
    folder = ctx.paths.secrets_path("garmin")
    email, password = read_credentials(folder)
    if not (email and password):
        ctx.log.error(
            "garmin_credentials_missing",
            action="Run `wearstream auth garmin`, or supply /secrets/garmin/email and password.",
        )
        raise SigninFailed("needs_person")
    claim_password_signin(ctx)
    client = watch.attach(library.make_client(ctx.values, email, password, prompt_mfa=watch.mfa))
    try:
        client.login(tokenstore=str(folder))
    except Exception as exc:
        raise classify(exc, "password", watch, ctx.log) from None
    return client
