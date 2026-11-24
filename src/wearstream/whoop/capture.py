"""The `range` and `snapshots` units (Whoop capture design)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import httpx

from wearstream.whoop import clock, http
from wearstream.whoop.api import Api
from wearstream.whoop.errors import OutOfTime, RateLimitedError, WhoopAPIError, WhoopAuthError
from wearstream.whoop.files import fingerprint
from wearstream.whoop.tokens import TokenManager

RANGE = (
    ("sleep", "/developer/v2/activity/sleep"),
    ("recovery", "/developer/v2/recovery"),
    ("workout", "/developer/v2/activity/workout"),
    ("cycle", "/developer/v2/cycle"),
)
SNAPSHOTS = (
    ("profile", "/developer/v2/user/profile/basic"),
    ("body_measurement", "/developer/v2/user/measurement/body"),
)
RANGE_COLLECTIONS = tuple(name for name, _path in RANGE)
PAGE_LIMIT, MAX_PAGES = 25, 20
Fetch = Callable[[Any, Api, str, str], None]


def _stamp(day: dt.date) -> str:
    return f"{day.isoformat()}T00:00:00.000Z"


# @spec WHOOP-CAPTURE-RANGE-002
def _capture(ctx: Any, collection: str, response: httpx.Response) -> None:
    url = response.request.url
    meta = {
        "http_status": response.status_code,
        "content_type": "application/json",
        "request_url": str(url),
        "request_params": dict(url.params.multi_items()),
    }
    ctx.capture(collection, response.content, meta, dedupe=True, dedupe_scope="folder", ext="json")


# @spec WHOOP-CAPTURE-RANGE-001, WHOOP-CAPTURE-RANGE-003, WHOOP-CAPTURE-PAGE-001
# @spec WHOOP-CAPTURE-PAGE-002, WHOOP-CAPTURE-PAGE-003, WHOOP-CAPTURE-PAGE-004
def _range(ctx: Any, api: Api, collection: str, path: str) -> None:
    """Every page of one collection for the unit's UTC day; empty pages end it uncaptured."""
    day = dt.date.fromisoformat(ctx.day)
    params = {
        "start": _stamp(day),
        "end": _stamp(day + dt.timedelta(days=1)),
        "limit": str(PAGE_LIMIT),
    }
    seen: set[str] = set()
    for _page in range(MAX_PAGES):
        response, body = api.get(path, params)
        records = body.get("records") if isinstance(body, dict) else None
        if not isinstance(records, list) or not records:
            return
        _capture(ctx, collection, response)
        cursor = body.get("next_token")
        if not cursor:
            return
        if cursor in seen:
            ctx.log.warning(
                "whoop_pagination_stopped", collection=collection, reason="cursor_repeated"
            )
            return
        seen.add(cursor)
        params = {**params, "nextToken": str(cursor)}
    ctx.log.warning(
        "whoop_pagination_stopped", collection=collection, reason="page_cap", pages=MAX_PAGES
    )


# @spec WHOOP-CAPTURE-SNAP-001
def _snapshot(ctx: Any, api: Api, collection: str, path: str) -> None:
    response, _body = api.get(path)
    _capture(ctx, collection, response)


def _fail(ctx: Any, collections: list[str], reason: str) -> None:
    for collection in collections:
        ctx.failed_request(f"{collection}: {reason}")


# @spec WHOOP-CAPTURE-RANGE-006, WHOOP-CAPTURE-SNAP-002, WHOOP-CAPTURE-TIME-002
# @spec WHOOP-CAPTURE-REPORT-001, WHOOP-CAPTURE-REPORT-002, WHOOP-CAPTURE-REPORT-003
# @spec WHOOP-CAPTURE-REPORT-004, WHOOP-AUTH-FP-002
def _run_plan(ctx: Any, api: Api, plan: tuple[tuple[str, str], ...], fetch: Fetch) -> None:
    names = [name for name, _path in plan]
    for index, (collection, path) in enumerate(plan):
        try:
            fetch(ctx, api, collection, path)
        except WhoopAuthError as exc:
            if exc.needs_person:
                ctx.needs_credential(fingerprint(ctx.paths.secrets_path("whoop")))
            else:
                _fail(ctx, names[index:], f"no token ({exc})")
            return
        except RateLimitedError as exc:
            ctx.log.warning(
                "whoop_rate_limited", collection=collection, retry_after=exc.retry_after
            )
            ctx.rate_limited(exc.retry_after)
            return
        except OutOfTime:
            ctx.log.warning("whoop_unit_out_of_time", collections=names[index:])
            _fail(ctx, names[index:], "too little time left in the unit")
            return
        except WhoopAPIError as exc:
            extra = {"error_type": exc.error_type} if exc.error_type else {}
            ctx.log.warning("whoop_request_failed", collection=collection, reason=str(exc), **extra)
            ctx.failed_request(f"{collection}: {exc}")
            if fetch is _snapshot:
                return


def run_unit(ctx: Any) -> None:
    """A `range` or `snapshots` unit; its deadline is its start plus the job's time limit."""
    deadline = clock.monotonic() + ctx.job.time_limit
    tokens = TokenManager(
        ctx.paths.secrets_path("whoop"),
        trigger=ctx.trigger,
        signals=ctx.signals,
        log=ctx.log,
        deadline=deadline,
    )
    limiter = http.RateLimiter(int(ctx.values["WEARSTREAM_WHOOP_REQUESTS_PER_MINUTE"]))
    with http.data_client() as client:
        api = Api(client, tokens, limiter, deadline, ctx.signals)
        if ctx.job.name == "range":
            _run_plan(ctx, api, RANGE, _range)
        else:
            _run_plan(ctx, api, SNAPSHOTS, _snapshot)
