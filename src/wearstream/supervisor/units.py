"""Units of work, the children running them, and how a finished unit is classified."""

from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass, field
from typing import Any

from wearstream.jobs import Job
from wearstream.runner import outcome_for

COUNT_KEYS = ("written", "skipped_identical", "skipped_non_2xx", "failed")


def new_run_id() -> str:
    return secrets.token_hex(6)


@dataclass(frozen=True)
class Unit:
    source: str
    job: str
    day: str | None
    trigger: str  # schedule, catchup, retry, backfill or manual
    run_id: str = field(default_factory=new_run_id)
    tick: str | None = None  # the tick that enqueued it; None for retries and backfill


@dataclass
class Running:
    unit: Unit
    job: Job
    handle: Any
    started_mono: float
    started_at: dt.datetime
    signalled: str | None = None  # "limit" or "shutdown"
    term_mono: float | None = None
    killed: bool = False


@dataclass
class Repair:
    unit: Unit
    job: Job
    days: list[str]
    retry: bool


def valid_result(raw: object) -> dict[str, Any] | None:
    """The child's result when it has every count as an integer; otherwise unknown."""
    if not isinstance(raw, dict):
        return None
    for key in COUNT_KEYS:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
    return raw


# @spec SUPERVISOR-OUTCOME-002, SUPERVISOR-OUTCOME-003
def classify(code: int, signalled: str | None) -> str:
    """What the supervisor did decides first; then the exit code."""
    if signalled == "limit":
        return "killed"
    if signalled == "shutdown":
        return "interrupted"
    return outcome_for(code)


# @spec SUPERVISOR-AFTER-004, SUPERVISOR-AFTER-005
def should_retry(trigger: str, outcome: str) -> bool:
    if trigger in ("schedule", "catchup") and outcome in ("killed", "crashed", "interrupted"):
        return True
    return trigger == "retry" and outcome == "interrupted"


def repair_days(unit: Unit, job: Job, started: dt.datetime, ended: dt.datetime) -> list[str]:
    """The partition day, or for a snapshot every UTC date from its start to its end."""
    if unit.day:
        return [unit.day]
    first, last = started.astimezone(dt.UTC).date(), max(started, ended).astimezone(dt.UTC).date()
    return [(first + dt.timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def run_fields(unit: Unit) -> dict[str, Any]:
    fields = {
        "run_id": unit.run_id,
        "source": unit.source,
        "job": unit.job,
        "trigger": unit.trigger,
    }
    return {**fields, "day": unit.day} if unit.day else fields
