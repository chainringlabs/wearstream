"""The `daily` and `reference` units (Garmin capture design)."""

from __future__ import annotations

import time
from typing import Any

from wearstream.garmin import catalog, clock, library, signin
from wearstream.garmin.calls import Caller, CallFailed, UnitOver, write_download, write_json
from wearstream.garmin.catalog import CATALOG, GOAL_STATUSES, Entry, selected
from wearstream.garmin.store import (
    display_path,
    fingerprint,
    problem_fields,
    session_saved,
    store_problem,
)


class _Run:
    """One unit's state: what it has called, and what it still owes a report for."""

    def __init__(self, ctx: Any, caller: Caller, owed: list[str]) -> None:
        self.ctx, self.caller, self.owed = ctx, caller, owed

    def done(self, name: str) -> None:
        if name in self.owed:
            self.owed.remove(name)

    def failed(self, name: str, reason: str) -> None:
        self.ctx.failed_request(f"{name}: {reason}")
        self.done(name)

    def fetch(self, entry: Entry, params: dict[str, Any], *args: Any, scope: str = "folder",
              **kwargs: Any) -> Any:  # fmt: skip
        value = self.caller.call(entry.method, *args, **kwargs)
        write_json(self.ctx, entry.name, value, entry.method, params, entry, scope)
        return value


def _discover(run: _Run, method: str) -> Any:
    try:
        return run.caller.call(method)
    except CallFailed:
        return None


def _device_ids(found: Any) -> list[Any] | None:
    if not isinstance(found, list):
        return None
    return [d["deviceId"] for d in found if isinstance(d, dict) and d.get("deviceId") is not None]


# @spec GARMIN-CAPTURE-DAILY-001, GARMIN-CAPTURE-DAILY-004
def _daily_entries(run: _Run, day: str, entries: list[Entry]) -> None:
    devices = None
    if any(e.kind == "per_device_range" for e in entries):
        devices = _device_ids(_discover(run, "get_devices"))
    for entry in entries:
        try:
            if entry.kind == "daily":
                run.fetch(entry, {"cdate": day}, day)
            elif entry.kind == "range":
                run.fetch(entry, {"start": day, "end": day}, day, day)
            elif devices is None:
                run.failed(entry.name, "device discovery failed")
                continue
            else:
                for device in devices:
                    params = {"device_id": device, "start": day, "end": day}
                    run.fetch(entry, params, str(device), day, day, scope="request")
            run.done(entry.name)
        except CallFailed as failure:
            run.failed(entry.name, str(failure))


# @spec GARMIN-CAPTURE-DAILY-002, GARMIN-CAPTURE-DAILY-003, GARMIN-CAPTURE-CAT-004
def _activities(run: _Run, day: str) -> None:
    values = run.ctx.values
    if not selected("activities", values):
        return
    entry = next(e for e in CATALOG if e.name == "activities")
    try:
        found = run.fetch(entry, {"start": day, "end": day}, day, day)
    except CallFailed as failure:
        run.failed("activities", str(failure))
        return
    run.done("activities")
    downloads = catalog.DOWNLOADS + (
        catalog.ALT_DOWNLOADS if values["WEARSTREAM_GARMIN_ALT_FORMATS"] else ()
    )
    for activity in found or []:
        activity_id = activity.get("activityId") if isinstance(activity, dict) else None
        if activity_id is None:
            continue
        for name, method in catalog.PER_ACTIVITY:
            if selected(name, values):
                try:
                    value = run.caller.call(method, activity_id)
                    write_json(
                        run.ctx, name, value, method, {"activity_id": activity_id}, None, "request"
                    )
                except CallFailed as failure:
                    run.failed(name, str(failure))
        for name, fmt in downloads:
            if selected(name, values):
                try:
                    raw = run.caller.call(
                        "download_activity", str(activity_id), library.DOWNLOAD_FORMAT[fmt]
                    )
                    write_download(run.ctx, name, raw, activity_id, fmt)
                except CallFailed as failure:
                    run.failed(name, str(failure))


# @spec GARMIN-CAPTURE-CAT-003
def daily(run: _Run) -> None:
    day = run.ctx.day
    entries = [e for e in CATALOG if e.kind in catalog.DAILY_KINDS and e.name != "activities"]
    _daily_entries(run, day, [e for e in entries if selected(e.name, run.ctx.values)])
    _activities(run, day)


# @spec GARMIN-CAPTURE-REF-001, GARMIN-CAPTURE-REF-002
def reference(run: _Run) -> None:
    entries = [
        e for e in CATALOG if e.kind in catalog.REFERENCE_KINDS and selected(e.name, run.ctx.values)
    ]
    devices = profile = None
    if any(e.kind == "per_device" for e in entries):
        devices = _device_ids(_discover(run, "get_devices"))
    if any(e.kind == "per_profile" for e in entries):
        found = _discover(run, "get_userprofile_settings")
        profile = found.get("id") if isinstance(found, dict) else None
    fetch_date = clock.utc_now().date().isoformat()
    for entry in entries:
        try:
            if entry.kind == "static":
                run.fetch(entry, {})
            elif entry.kind == "static_goals":
                for status in GOAL_STATUSES:
                    run.fetch(entry, {"status": status}, scope="request", status=status)
            elif entry.kind == "weekly":
                run.fetch(entry, {"end": fetch_date, "weeks": 4}, fetch_date, 4)
            elif entry.kind == "per_profile":
                if profile is None:
                    run.failed(entry.name, "profile discovery failed")
                    continue
                run.fetch(entry, {"user_profile_number": profile}, profile)
            elif devices is None:
                run.failed(entry.name, "device discovery failed")
                continue
            else:
                for device in devices:
                    run.fetch(entry, {"device_id": device}, device, scope="request")
            run.done(entry.name)
        except CallFailed as failure:
            run.failed(entry.name, str(failure))


def _report_signin(ctx: Any, failure: signin.SigninFailed) -> None:
    if failure.kind == "needs_person":
        ctx.needs_credential(fingerprint(ctx.paths.secrets_path("garmin")))
    elif failure.kind == "rate_limited":
        ctx.rate_limited(failure.retry_after)
    else:
        ctx.failed_request("sign-in failed")


def _planned(ctx: Any) -> list[str]:
    from wearstream.garmin import GarminSource

    if ctx.job.name == "daily":
        return list(GarminSource().collections("daily", ctx.values))
    return [
        e.name
        for e in CATALOG
        if e.kind in catalog.REFERENCE_KINDS and selected(e.name, ctx.values)
    ]


# @spec GARMIN-AUTH-GUARD-001, GARMIN-AUTH-GUARD-002, GARMIN-AUTH-GUARD-003, GARMIN-AUTH-FP-002
# @spec GARMIN-AUTH-LOG-001
def run_unit(ctx: Any) -> None:
    """Sign in once, then call the job's entries with that client."""
    library.prepare(ctx.values, ctx.log)
    folder = ctx.paths.secrets_path("garmin")
    problem = store_problem(folder)
    if problem:
        ctx.log.error("garmin_tokenstore_unusable", **problem_fields(folder, problem))
    started_ns, deadline = time.time_ns(), clock.monotonic() + ctx.job.time_limit
    try:
        client = signin.sign_in(ctx)
    except signin.SigninFailed as failure:
        _report_signin(ctx, failure)
        return
    run = _Run(ctx, Caller(ctx, client, deadline), _planned(ctx))
    try:
        (daily if ctx.job.name == "daily" else reference)(run)
    except UnitOver as over:
        _unit_over(run, over)
    finally:
        if not session_saved(folder, client.client.dumps(), started_ns):
            ctx.log.error(
                "garmin_tokenstore_not_saved",
                path=display_path(folder),
                action="Make /secrets/garmin writable by the container user, and mounted.",
            )


def _unit_over(run: _Run, over: UnitOver) -> None:
    if over.kind == "rate_limited":
        run.ctx.rate_limited(None)
    elif over.kind == "refused":
        run.ctx.failed_request("the session was refused part-way; the next unit signs in again")
    else:
        run.ctx.log.warning("garmin_unit_out_of_time", collections=list(run.owed))
        for name in list(run.owed):
            run.failed(name, "too little time left in the unit")
