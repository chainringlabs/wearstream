"""A hand-started `wearstream run` (supervisor design, *Hand-started runs*)."""

from __future__ import annotations

import datetime as dt
import os
import secrets
import signal
import threading
import time
from typing import Any

import wearstream.runner as runner
from wearstream import capture, log
from wearstream.jobs import Job
from wearstream.runner import BUSY, ENVIRONMENT, NEEDS_AUTH, RATE_LIMITED, outcome_for
from wearstream.runner.context import RunContext, Signals
from wearstream.runner.run import execute, lock_path
from wearstream.state import StateStore, iso, parse_time, pid_namespace, release, try_lock
from wearstream.state.refill import record_refill

HEARTBEAT_FRESH_SECONDS = 120


def _paused_or_unfit(store: StateStore, resolved: Any, now: dt.datetime) -> dict[str, Any] | None:
    beat = store.read_quiet("heartbeat.json") or {}
    updated = parse_time(beat.get("updated_at"))
    if updated and (now - updated).total_seconds() <= HEARTBEAT_FRESH_SECONDS:
        pause = beat.get("pause")
        if pause:
            return {
                "code": ENVIRONMENT,
                "reason": f"capture is paused: {pause.get('reason')}",
                "action": "Fix what the daemon's capture_paused line names; it resumes on its own.",
            }
        return None
    result = capture.self_test(resolved.data)
    if result.ok:
        return None
    return {
        "code": ENVIRONMENT,
        "reason": f"/data failed its self-test at the {result.step} step: {result.error}",
        "action": "Make /data writable by the container user, on a filesystem with hard links.",
    }


def _source_refusal(store: StateStore, source: str, now: dt.datetime) -> dict[str, Any] | None:
    doc = store.read_quiet(f"sources/{source}.json") or {}
    if doc.get("state") == "waiting_for_auth":
        return {
            "code": NEEDS_AUTH,
            "reason": f"{source} is waiting for a new credential",
            "action": f"Run `wearstream auth {source}` (for example "
            f"`docker exec -it <container> wearstream auth {source}`).",
        }
    until = parse_time(doc.get("cooldown_until"))
    if until and until > now:
        return {
            "code": RATE_LIMITED,
            "reason": f"{source} is rate-limited",
            "cooldown_until": doc["cooldown_until"],
            "action": f"Wait until {doc['cooldown_until']}, when the vendor's limit has passed.",
        }
    return None


# @spec SUPERVISOR-MANUAL-002, SUPERVISOR-MANUAL-003, SUPERVISOR-MANUAL-008
def _refusal(store: StateStore, resolved: Any, source: str) -> dict[str, Any] | None:
    now = dt.datetime.now(dt.UTC)
    return _paused_or_unfit(store, resolved, now) or _source_refusal(store, source, now)


# @spec SUPERVISOR-MANUAL-007
def _limit_timers(seconds: float) -> list[threading.Timer]:
    """Stop this run at its time limit, and end it outright after the grace."""
    pid = os.getpid()
    timers = [
        threading.Timer(seconds, os.kill, (pid, signal.SIGTERM)),
        threading.Timer(seconds + runner.STOP_GRACE_SECONDS, os.kill, (pid, signal.SIGKILL)),
    ]
    for timer in timers:
        timer.daemon = True
        timer.start()
    return timers


def _line(kind: str, run_id: str, job: Job, day: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "type": kind,
        "run_id": run_id,
        "source": job.source,
        "job": job.name,
        "day": day,
        "trigger": "manual",
        "origin": "manual",
        **extra,
    }


def _finish_line(
    run_id: str, job: Job, day: str | None, code: int, began: float, ctx: RunContext
) -> dict[str, Any]:
    return _line(
        "finish",
        run_id,
        job,
        day,
        outcome=outcome_for(code),
        exit_code=code,
        duration_s=round(time.monotonic() - began, 3),
        counts=ctx.counts,
        error=ctx.result()["error"],
    )


# @spec SUPERVISOR-MANUAL-009
def _manual_checks(
    job: Job, values: dict, resolved: Any, store: StateStore, run_id: str, logger: Any
) -> int:
    fd = try_lock(lock_path(resolved, job))
    if fd is None:
        logger.warning("run_busy", reason="the checks lock is held by another run")
        return BUSY
    try:
        store.append_run(_line("start", run_id, job, None), dt.datetime.now(dt.UTC))
        signals, began = Signals(), time.monotonic()
        signals.install()
        ctx = RunContext(job, None, "manual", run_id, values, resolved, logger, signals)
        code = execute(ctx, logger)
        store.append_run(_finish_line(run_id, job, None, code, began, ctx), dt.datetime.now(dt.UTC))
        return code
    finally:
        release(fd)


def _inflight(store: StateStore, entry: dict[str, Any] | None, run_id: str) -> None:
    def change(doc: dict[str, Any] | None) -> dict[str, Any]:
        units = [u for u in (doc or {}).get("units", []) if u.get("run_id") != run_id]
        return {"units": units + ([entry] if entry else [])}

    store.update("inflight.json", change)


def _run_locked(
    job: Job,
    day: str | None,
    values: dict,
    resolved: Any,
    store: StateStore,
    run_id: str,
    logger: Any,
) -> int:
    now = dt.datetime.now(dt.UTC)
    entry = {
        **_line("start", run_id, job, day),
        "started_at": iso(now),
        "pid": os.getpid(),
        "pidns": pid_namespace(),
    }
    entry.pop("type")
    _inflight(store, entry, run_id)
    store.append_run(_line("start", run_id, job, day), now)
    signals, began = Signals(), time.monotonic()
    signals.install()
    timers = _limit_timers(job.time_limit)
    ctx = RunContext(job, day, "manual", run_id, values, resolved, logger, signals)
    code = execute(ctx, logger)
    for timer in timers:
        timer.cancel()
    store.append_run(_finish_line(run_id, job, day, code, began, ctx), dt.datetime.now(dt.UTC))
    if day:
        record_refill(store, job.source, job.name, day, outcome_for(code) == "success")
    _inflight(store, None, run_id)
    return code


# @spec SUPERVISOR-MANUAL-001, SUPERVISOR-LOG-002
def run_manual(job: Job, day: str | None, values: dict, resolved: Any) -> int:
    """Refuse when the daemon or the source says no; otherwise run under the source lock."""
    store = StateStore(resolved.state)
    run_id = secrets.token_hex(6)
    logger = log.get_logger("run").bind(
        run_id=run_id,
        source=job.source,
        job=job.name,
        trigger="manual",
        **({"day": day} if day else {}),
    )
    if job.kind == "checks":
        return _manual_checks(job, values, resolved, store, run_id, logger)
    refusal = _refusal(store, resolved, job.source)
    if refusal:
        code = refusal.pop("code")
        logger.error("run_refused", **refusal)
        return code
    fd = try_lock(lock_path(resolved, job))
    if fd is None:
        logger.warning("run_busy", reason=f"another run of {job.source} holds its lock")
        return BUSY
    try:
        return _run_locked(job, day, values, resolved, store, run_id, logger)
    finally:
        release(fd)
