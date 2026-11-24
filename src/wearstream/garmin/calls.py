"""Calling the library inside a unit, and writing what it returns
(Garmin capture design, *What is written* and *What the unit reports*)."""

from __future__ import annotations

import json
from typing import Any

from wearstream.capture import screen_secrets
from wearstream.garmin import clock, library
from wearstream.garmin.catalog import DOWNLOAD_EXT, Entry
from wearstream.garmin.signin import rate_limited
from wearstream.runner.context import Stopped

MARGIN_SECONDS = 180.0  # one call with the library's own retries can take about two minutes


class CallFailed(Exception):
    """One call raised; the unit reports it and moves on."""


class UnitOver(Exception):
    """The unit makes no further calls: `kind` is `rate_limited`, `refused` or `out_of_time`."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class Caller:
    """Every library call of a unit: pacing, the deadline, stop signals, and failures."""

    def __init__(self, ctx: Any, client: Any, deadline: float) -> None:
        self.ctx, self.client, self.deadline = ctx, client, deadline
        self.pacing = float(ctx.values["WEARSTREAM_GARMIN_PACING"])
        self.called = False

    def _room(self, wait: float) -> bool:
        return self.deadline - clock.monotonic() - wait >= MARGIN_SECONDS

    # @spec GARMIN-CAPTURE-DAILY-005, GARMIN-CAPTURE-REPORT-001, GARMIN-CAPTURE-REPORT-002
    # @spec GARMIN-CAPTURE-REPORT-003, GARMIN-AUTH-SIGNIN-005
    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self.ctx.signals.received:
            raise Stopped
        pause = self.pacing if self.called else 0.0
        if not self._room(pause):
            raise UnitOver("out_of_time")
        if pause:
            clock.sleep(pause)
        self.called = True
        try:
            return getattr(self.client, method)(*args, **kwargs)
        except library.GarminConnectAuthenticationError:
            self.ctx.log.warning("garmin_signin_failed", step="call", method=method)
            raise UnitOver("refused") from None
        except Exception as exc:
            if rate_limited(exc):
                self.ctx.log.warning("garmin_rate_limited", step="call", method=method)
                raise UnitOver("rate_limited") from None
            self.ctx.log.warning("garmin_call_failed", method=method, error_type=type(exc).__name__)
            raise CallFailed(type(exc).__name__) from None


def _scope(keep_every: bool, scope: str) -> dict[str, Any]:
    return {"dedupe": False} if keep_every else {"dedupe": True, "dedupe_scope": scope}


# @spec GARMIN-CAPTURE-WRITE-001, GARMIN-CAPTURE-WRITE-002, GARMIN-CAPTURE-WRITE-003
# @spec GARMIN-CAPTURE-WRITE-004, GARMIN-CAPTURE-WRITE-005, GARMIN-CAPTURE-WRITE-006
# @spec GARMIN-CAPTURE-DEDUPE-001, GARMIN-CAPTURE-DEDUPE-002, GARMIN-CAPTURE-DEDUPE-003
def write_json(
    ctx: Any,
    collection: str,
    value: Any,
    method: str,
    params: dict[str, Any],
    entry: Entry | None,
    scope: str,
) -> None:
    """Compact JSON in the library's key order; `None`, and empties of a flagged entry, skipped."""
    if value is None:
        return
    if entry and entry.skip_if_empty and isinstance(value, list | dict) and not value:
        return
    meta: dict[str, Any] = {
        "http_status": 200,
        "content_type": "application/json",
        "capture_mode": "reserialized",
        "request_url": method,
        "request_params": params,
    }
    if entry and entry.screen_secrets:
        value, meta["redacted_fields"] = screen_secrets(value)
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    keep_every = bool(entry and entry.keeps_every_capture)
    ctx.capture(collection, body, meta, ext="json", **_scope(keep_every, scope))


def write_download(ctx: Any, collection: str, raw: Any, activity_id: Any, fmt: str) -> None:
    """An activity file byte for byte, compared per request."""
    if raw is None:
        return
    ext, content_type = DOWNLOAD_EXT[fmt]
    meta = {
        "http_status": 200,
        "content_type": content_type,
        "capture_mode": "raw",
        "request_url": "download_activity",
        "request_params": {"activity_id": activity_id, "format": fmt},
    }
    ctx.capture(collection, bytes(raw), meta, ext=ext, dedupe=True, dedupe_scope="request")
