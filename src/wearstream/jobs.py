"""Each source's job declarations, with the operator's overrides applied, and the schedule
guardrails configuration validation enforces (supervisor design, *Jobs*, *Schedules*)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from wearstream.cron import Cron

SECONDS_PER_MINUTE = 60  # time-limit settings are in minutes
MAX_LONGEST_GAP = dt.timedelta(hours=24)


@dataclass(frozen=True)
class Declaration:
    kind: str  # partitioned, snapshot or checks
    include_today: bool = False


# What each source declares (its capture design); the tunable values come from settings.
DECLARATIONS = {
    "whoop": {
        "range": Declaration("partitioned", include_today=True),
        "snapshots": Declaration("snapshot"),
        "checks": Declaration("checks"),
    },
    "garmin": {
        "daily": Declaration("partitioned", include_today=False),
        "reference": Declaration("snapshot"),
        "checks": Declaration("checks"),
    },
}


def setting(source: str, job: str, field: str) -> str:
    """The setting that holds `field` for a job (Whoop's capture jobs share one schedule)."""
    if field == "SCHEDULE" and source == "whoop" and job in ("range", "snapshots"):
        return "WEARSTREAM_WHOOP_SCHEDULE"
    return f"WEARSTREAM_{source.upper()}_{job.upper()}_{field}"


@dataclass(frozen=True)
class Job:
    source: str
    name: str
    kind: str
    schedule: str
    window_days: int | None
    include_today: bool
    time_limit: int  # seconds
    backfill_pause: int  # seconds

    @property
    def key(self) -> str:
        return f"{self.source}.{self.name}"

    @cached_property
    def cron(self) -> Cron:
        return Cron(self.schedule)

    def window(self, fire_date: dt.date) -> list[str]:
        """The partition days a tick on `fire_date` covers, newest first."""
        end = fire_date if self.include_today else fire_date - dt.timedelta(days=1)
        return [(end - dt.timedelta(days=i)).isoformat() for i in range(self.window_days or 0)]

    @property
    def worst_case(self) -> int:
        return self.time_limit * (self.window_days or 1)


def _job(values: dict[str, Any], source: str, name: str, declaration: Declaration) -> Job:
    partitioned = declaration.kind == "partitioned"
    return Job(
        source=source,
        name=name,
        kind=declaration.kind,
        schedule=values[setting(source, name, "SCHEDULE")],
        window_days=values[setting(source, name, "WINDOW_DAYS")] if partitioned else None,
        include_today=declaration.include_today,
        time_limit=values[setting(source, name, "TIME_LIMIT")] * SECONDS_PER_MINUTE,
        backfill_pause=values[setting(source, name, "BACKFILL_PAUSE")] if partitioned else 0,
    )


# @spec SUPERVISOR-JOB-001
def effective_jobs(values: dict[str, Any]) -> dict[tuple[str, str], Job]:
    """Every enabled source's jobs, with the operator's overrides applied."""
    return {
        (source, name): _job(values, source, name, declaration)
        for source in values.get("WEARSTREAM_SOURCES", [])
        for name, declaration in DECLARATIONS[source].items()
    }


def _minutes(seconds: float) -> str:
    return f"{seconds / SECONDS_PER_MINUTE:g}"


def _schedule_problems(job: Job) -> list[str]:
    name = setting(job.source, job.name, "SCHEDULE")
    try:
        gap = job.cron.longest_gap
    except ValueError as exc:
        return [f"{name} = {job.schedule!r}: {exc}"]
    if gap > MAX_LONGEST_GAP:
        hours = gap.total_seconds() / 3600
        return [
            f"{name} = {job.schedule!r} leaves up to {hours:g} hours between runs; "
            "every job must run at least once every 24 hours"
        ]
    return []


def _fit_problems(source: str, jobs: list[Job]) -> list[str]:
    problems = []
    capture_jobs = [j for j in jobs if j.kind != "checks"]
    partitioned = [j.time_limit for j in capture_jobs if j.kind == "partitioned"]
    needed = sum(j.worst_case for j in capture_jobs) + max(partitioned, default=0)
    shortest = min(j.cron.interval.total_seconds() for j in capture_jobs)
    if needed > shortest:
        problems.append(
            f"{source}: its jobs can need {_minutes(needed)} minutes of work per tick, "
            f"more than the {_minutes(shortest)} minutes between ticks; "
            "lower its time limits or window, or run it less often"
        )
    for job in jobs:
        if job.kind == "checks" and job.time_limit > job.cron.interval.total_seconds():
            problems.append(
                f"{setting(source, job.name, 'TIME_LIMIT')} is longer than the "
                f"{_minutes(job.cron.interval.total_seconds())} minutes between its runs"
            )
    return problems


# @spec SUPERVISOR-JOB-002, SUPERVISOR-JOB-003
def guardrail_problems(values: dict[str, Any]) -> list[str]:
    """Schedule and capacity problems of the enabled sources' jobs, after overrides."""
    problems: list[str] = []
    for source in values.get("WEARSTREAM_SOURCES", []):
        try:
            jobs = [
                j
                for (s, _), j in effective_jobs({**values, "WEARSTREAM_SOURCES": [source]}).items()
                if s == source
            ]
        except KeyError:
            continue  # a job setting is already reported invalid
        schedule_problems = [p for job in jobs for p in _schedule_problems(job)]
        problems += schedule_problems or _fit_problems(source, jobs)
    return problems
