"""`wearstream run`: checking its arguments, then running a unit for the daemon or by hand."""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

from wearstream import log
from wearstream.jobs import Job, effective_jobs
from wearstream.runner import BUSY, CRASHED, USAGE
from wearstream.runner.context import EndUnit, RunContext, Signals, Stopped
from wearstream.settings import apply_umask, ensure_valid, load, paths
from wearstream.sources import get_source
from wearstream.state import release, try_lock


def lock_path(resolved: Any, job: Job) -> Any:
    name = f"{job.source}.checks.lock" if job.kind == "checks" else f"{job.source}.lock"
    return resolved.state_path("locks", name)


def _usage(job_key: tuple[str, str], jobs: dict, day: str | None) -> str | None:
    source, name = job_key
    if job_key not in jobs:
        return f"{source} has no job named {name}, or {source} is not in WEARSTREAM_SOURCES"
    job = jobs[job_key]
    if job.kind == "partitioned" and not day:
        return f"the {name} job needs --day YYYY-MM-DD (the partition day to capture)"
    if job.kind != "partitioned" and day:
        return f"the {name} job takes no --day"
    if day:
        try:
            dt.date.fromisoformat(day)
        except ValueError:
            return "--day must be a date written YYYY-MM-DD"
    return None


def execute(ctx: RunContext, logger: Any) -> int:
    """Run the source's job; the exit code describes the run (SUPERVISOR-CHILD-005)."""
    try:
        try:
            get_source(ctx.source).run(ctx)
        except EndUnit:
            pass
    except Stopped:
        ctx.stopped = True
    except Exception as exc:
        ctx.signals.raising = False
        logger.warning("run_exception", **log.exception_fields(exc))
        return CRASHED
    ctx.signals.raising = False
    return ctx.exit_code()


def _write_result(fd: int | None, result: dict[str, Any]) -> None:
    if fd is None:
        return
    try:
        view = memoryview(json.dumps(result).encode())
        while view:
            view = view[os.write(fd, view) :]
        os.close(fd)
    except OSError:
        pass


# @spec SUPERVISOR-CHILD-002, SUPERVISOR-CHILD-006, SUPERVISOR-CHILD-007
def _daemon_unit(
    job: Job,
    day: str | None,
    trigger: str,
    run_id: str,
    result_fd: int | None,
    values: dict,
    resolved: Any,
) -> int:
    logger = log.get_logger("run").bind(
        run_id=run_id,
        source=job.source,
        job=job.name,
        trigger=trigger,
        **({"day": day} if day else {}),
    )
    signals = Signals()
    signals.install()
    fd = try_lock(lock_path(resolved, job))
    if fd is None:
        return BUSY
    try:
        ctx = RunContext(job, day, trigger, run_id, values, resolved, logger, signals)
        code = execute(ctx, logger)
        _write_result(result_fd, ctx.result())
        return code
    finally:
        release(fd)


# @spec SUPERVISOR-MANUAL-006
def run_command(
    source: str,
    job_name: str,
    day: str | None,
    run_id: str | None,
    trigger: str | None,
    result_fd: int | None,
) -> int:
    """`wearstream run`: a daemon's child when it has a run id, otherwise a hand-started run."""
    loaded = load(os.environ, "run")
    ensure_valid(loaded, "run")
    values = loaded.values
    apply_umask(values["WEARSTREAM_UMASK"])
    log.configure(fmt=values["WEARSTREAM_LOG_FORMAT"], level=values["WEARSTREAM_LOG_LEVEL"])
    log.quiet_libraries(garmin_debug=bool(values.get("WEARSTREAM_GARMIN_LIBRARY_DEBUG")))
    jobs = effective_jobs(values)
    problem = _usage((source, job_name), jobs, day)
    if problem:
        log.get_logger("run").error(
            "run_refused", reason=problem, action="Run `wearstream run --help` for usage."
        )
        return USAGE
    job, resolved = jobs[source, job_name], paths(values)
    if run_id:
        return _daemon_unit(job, day, trigger or "schedule", run_id, result_fd, values, resolved)
    from wearstream.runner.manual import run_manual

    return run_manual(job, day, values, resolved)
