"""The Whoop source's failures. Messages carry type names and statuses only, never text that
came from a request or a response (credential-safe logging)."""

from __future__ import annotations


class WhoopAuthError(Exception):
    """Obtaining a token failed. `needs_person` marks a needed credential; the data-request
    retry never retries this."""

    def __init__(self, message: str, *, needs_person: bool = False, event: str = "") -> None:
        super().__init__(message)
        self.needs_person, self.event = needs_person, event


class WhoopAPIError(Exception):
    """A data request failed; `error_type` names an unexpected exception's type."""

    def __init__(self, message: str, error_type: str | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type


class RateLimitedError(Exception):
    """Whoop still answered 429 after the retries."""

    def __init__(self, retry_after: float | None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class OutOfTime(Exception):
    """Too little of the unit's time limit is left to start another request or wait."""
