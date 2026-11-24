"""Every operator setting, with its default and bounds (operator-interface design).

The registry is the single source of each setting's name, type, default and limits. Job settings
also name the source and job they override, which decides which commands read them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SOURCES = ("whoop", "garmin")
# Each source's jobs, and which of them are partitioned (take a window and a backfill pause).
JOBS = {
    "whoop": {"range": True, "snapshots": False, "checks": False},
    "garmin": {"daily": True, "reference": False, "checks": False},
}
JOB_FIELDS = ("SCHEDULE", "WINDOW_DAYS", "TIME_LIMIT", "BACKFILL_PAUSE")
MAX_TIME_LIMIT = 360  # minutes: the supervisor's six-hour ceiling


@dataclass(frozen=True)
class Setting:
    name: str
    kind: str  # bool, int, list, date, cron, path, choice, umask, sources, str
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    source: str | None = None  # the source this setting belongs to, if any
    job: str | None = None  # the job it overrides, if any
    group: str = "general"  # path, logging, general, source


def _job(source: str, job: str, field: str, kind: str, default: Any, **bounds: Any) -> Setting:
    return Setting(
        f"WEARSTREAM_{source.upper()}_{job.upper()}_{field}",
        kind,
        default,
        source=source,
        job=job,
        group="source",
        **bounds,
    )


def _src(source: str, suffix: str, kind: str, default: Any = None, **bounds: Any) -> Setting:
    name = f"WEARSTREAM_{source.upper()}_{suffix}"
    return Setting(name, kind, default, source=source, group="source", **bounds)


_TIME = {"maximum": MAX_TIME_LIMIT}
_ENTRIES = [
    Setting("WEARSTREAM_DATA_DIR", "path", "/data", group="path"),
    Setting("WEARSTREAM_STATE_DIR", "path", "/state", group="path"),
    Setting("WEARSTREAM_SECRETS_DIR", "path", "/secrets", group="path"),
    Setting("WEARSTREAM_SOURCES", "sources"),
    # Whoop: range and snapshots share one schedule (one hourly token refresh serves both).
    _src("whoop", "SCHEDULE", "cron", "17 * * * *"),
    _job("whoop", "range", "WINDOW_DAYS", "int", 8, minimum=1, maximum=30),
    _job("whoop", "range", "TIME_LIMIT", "int", 5, minimum=3, **_TIME),
    _job("whoop", "snapshots", "TIME_LIMIT", "int", 5, minimum=3, **_TIME),
    _job("whoop", "range", "BACKFILL_PAUSE", "int", 60, minimum=30),
    _job("whoop", "checks", "SCHEDULE", "cron", "37 * * * *"),
    _job("whoop", "checks", "TIME_LIMIT", "int", 10, minimum=3, **_TIME),
    _job("garmin", "daily", "SCHEDULE", "cron", "30 5 * * *"),
    _job("garmin", "reference", "SCHEDULE", "cron", "30 5 * * *"),
    _job("garmin", "daily", "WINDOW_DAYS", "int", 14, minimum=1, maximum=60),
    _job("garmin", "daily", "TIME_LIMIT", "int", 15, minimum=5, **_TIME),
    _job("garmin", "reference", "TIME_LIMIT", "int", 10, minimum=5, **_TIME),
    _job("garmin", "daily", "BACKFILL_PAUSE", "int", 60, minimum=30),
    _job("garmin", "checks", "SCHEDULE", "cron", "7 3-21/6 * * *"),
    _job("garmin", "checks", "TIME_LIMIT", "int", 10, minimum=3, **_TIME),
    _src("whoop", "BACKFILL_FROM", "date"),
    _src("garmin", "BACKFILL_FROM", "date"),
    _src("whoop", "REDIRECT_URI", "str", "http://localhost:8000/callback"),
    _src("whoop", "REQUESTS_PER_MINUTE", "int", 60, minimum=1, maximum=90),
    _src("garmin", "CHINA", "bool", False),
    _src("garmin", "SELECT", "list", []),
    _src("garmin", "EXCLUDE", "list", []),
    _src("garmin", "ALT_FORMATS", "bool", False),
    _src("garmin", "PACING", "int", 2, minimum=2),
    _src("garmin", "EXPECTED_EMPTY", "list"),
    _src("garmin", "LIBRARY_DEBUG", "bool", False),
    Setting(
        "WEARSTREAM_LOG_FORMAT", "choice", "console", choices=("console", "json"), group="logging"
    ),
    Setting("WEARSTREAM_LOG_LEVEL", "choice", "INFO", choices=("INFO", "DEBUG"), group="logging"),
    Setting("WEARSTREAM_UMASK", "umask", "022", group="logging"),
    Setting("WEARSTREAM_FAILING_TICKS", "int", 3, minimum=1, maximum=100),
]

REGISTRY: dict[str, Setting] = {setting.name: setting for setting in _ENTRIES}
# Names an earlier release used: ignored with a warning, never refused.
RETIRED: frozenset[str] = frozenset()
