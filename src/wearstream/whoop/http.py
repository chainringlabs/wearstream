"""Whoop's addresses, the HTTP clients, and the per-unit rate limiter."""

from __future__ import annotations

from collections import deque

import httpx

from wearstream.whoop import clock

BASE_URL = "https://api.prod.whoop.com"
TOKEN_URL = f"{BASE_URL}/oauth/oauth2/token"
AUTHORIZE_URL = f"{BASE_URL}/oauth/oauth2/auth"
REQUEST_TIMEOUT = 30.0
WINDOW_SECONDS = 60.0
TRANSPORT: httpx.BaseTransport | None = None  # tests put a stand-in Whoop here


# @spec WHOOP-CAPTURE-REQ-001
def data_client() -> httpx.Client:
    """One client per unit for data requests, never used for the token endpoint."""
    return httpx.Client(base_url=BASE_URL, timeout=REQUEST_TIMEOUT, transport=TRANSPORT)


# @spec WHOOP-CAPTURE-REQ-002
class RateLimiter:
    """A sliding window of attempts at 90% of the configured rate, rounded down, at least 1."""

    def __init__(self, per_minute: int) -> None:
        self.limit = max(1, per_minute * 9 // 10)
        self.times: deque[float] = deque()

    def delay(self) -> float:
        now = clock.monotonic()
        while self.times and now - self.times[0] >= WINDOW_SECONDS:
            self.times.popleft()
        if len(self.times) < self.limit:
            return 0.0
        return WINDOW_SECONDS - (now - self.times[0])

    def wait(self) -> None:
        pause = self.delay()
        if pause > 0:
            clock.sleep(pause)
            self.delay()
        self.times.append(clock.monotonic())
