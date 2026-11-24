"""Whoop data requests: bearer token, rate limit, retry, and the unit's deadline
(Whoop capture design, *Data requests* and *Staying inside the time limit*)."""

from __future__ import annotations

from typing import Any

import httpx

from wearstream.runner.context import Stopped
from wearstream.whoop import clock
from wearstream.whoop.errors import OutOfTime, RateLimitedError, WhoopAPIError
from wearstream.whoop.http import REQUEST_TIMEOUT, RateLimiter
from wearstream.whoop.tokens import TokenManager

ATTEMPTS = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}
DEFAULT_WAIT_SECONDS = 2.0
RETRY_AFTER_CAP_SECONDS = 60.0
MARGIN_SECONDS = 60.0  # left for one credential exchange to finish before the time limit


def _retry_after(response: httpx.Response) -> float | None:
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return min(max(seconds, 0.0), RETRY_AFTER_CAP_SECONDS)


class Api:
    """One unit's data requests, sharing its client, token, limiter and deadline."""

    def __init__(
        self,
        client: httpx.Client,
        tokens: TokenManager,
        limiter: RateLimiter,
        deadline: float,
        signals: Any,
    ) -> None:
        self.client, self.tokens, self.limiter = client, tokens, limiter
        self.deadline, self.signals = deadline, signals

    # @spec WHOOP-CAPTURE-TIME-001
    def _room(self, seconds: float) -> bool:
        return self.deadline - clock.monotonic() - seconds >= MARGIN_SECONDS

    # @spec WHOOP-CAPTURE-STOP-001, WHOOP-AUTH-REFRESH-008
    def _start(self) -> str:
        """Everything before an attempt: a pending stop, the deadline, the token, the limiter."""
        if self.signals.received:
            raise Stopped
        pause = self.limiter.delay()
        if not self._room(pause + REQUEST_TIMEOUT):
            raise OutOfTime
        token = self.tokens.access_token()  # a WhoopAuthError is never retried here
        self.limiter.wait()
        return token

    def _attempt(self, path: str, params: dict[str, str] | None) -> httpx.Response | str:
        """A response, or the name of a failure worth retrying."""
        token = self._start()
        try:
            return self.client.get(
                path, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return type(exc).__name__
        except Exception as exc:
            raise WhoopAPIError(type(exc).__name__, error_type=type(exc).__name__) from None

    # @spec WHOOP-CAPTURE-REQ-003, WHOOP-CAPTURE-REQ-004, WHOOP-CAPTURE-REQ-005
    # @spec WHOOP-CAPTURE-RANGE-004, WHOOP-CAPTURE-RANGE-005, WHOOP-AUTH-REFRESH-013
    def get(self, path: str, params: dict[str, str] | None = None) -> tuple[httpx.Response, Any]:
        """A 2xx response and its parsed JSON; raises for everything else."""
        failure, retry_after = "", None
        for attempt in range(1, ATTEMPTS + 1):
            outcome = self._attempt(path, params)
            wait = DEFAULT_WAIT_SECONDS
            if isinstance(outcome, str):
                failure, retry_after = outcome, None
            elif 200 <= outcome.status_code < 300:
                try:
                    return outcome, outcome.json()
                except ValueError:
                    raise WhoopAPIError("the response was not JSON") from None
            elif outcome.status_code == 401:
                self.tokens.expire()
                raise WhoopAPIError("HTTP 401: the access token was refused")
            elif outcome.status_code == 403:
                raise self.tokens.scope_missing()
            elif outcome.status_code in RETRY_STATUSES:
                failure, retry_after = f"HTTP {outcome.status_code}", _retry_after(outcome)
                wait = retry_after if retry_after is not None else DEFAULT_WAIT_SECONDS
            else:
                raise WhoopAPIError(f"HTTP {outcome.status_code}")
            if attempt == ATTEMPTS:
                break
            if not self._room(wait + REQUEST_TIMEOUT):
                raise OutOfTime
            clock.sleep(wait)
        if failure == "HTTP 429":
            raise RateLimitedError(retry_after)
        raise WhoopAPIError(f"{failure} after {ATTEMPTS} attempts")
