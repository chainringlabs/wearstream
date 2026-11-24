"""Time as the Garmin source reads it; one place, so tests can control it."""

from __future__ import annotations

import datetime as dt
import time


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def monotonic() -> float:
    return time.monotonic()


def sleep(seconds: float) -> None:
    time.sleep(seconds)
