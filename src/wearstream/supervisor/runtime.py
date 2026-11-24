"""What the supervisor tracks per source, per job and per tick, and how it persists them."""

from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from wearstream.jobs import Job
from wearstream.state import iso, parse_time
from wearstream.supervisor.units import Repair, Running, Unit


def _iso(moment: dt.datetime | None) -> str | None:
    return iso(moment) if moment else None


@dataclass
class Tick:
    job: tuple[str, str]
    at: dt.datetime
    trigger: str
    pending: set[str]
    outcomes: list[str] = field(default_factory=list)


@dataclass
class JobSchedule:
    job: Job
    next_fire: dt.datetime | None = None
    last_tick: dt.datetime | None = None
    last_completed: dt.datetime | None = None
    failed_ticks: int = 0
    failing: bool = False
    backfill_day: str | None = None  # the UTC day a successful scheduled tick last started a pass

    def load(self, doc: dict[str, Any]) -> None:
        self.last_tick = parse_time(doc.get("last_tick"))
        self.last_completed = parse_time(doc.get("last_completed_tick"))
        self.failed_ticks = int(doc.get("consecutive_failed_ticks") or 0)
        self.failing = bool(doc.get("failing"))

    def doc(self) -> dict[str, Any]:
        return {
            "last_tick": _iso(self.last_tick),
            "last_completed_tick": _iso(self.last_completed),
            "consecutive_failed_ticks": self.failed_ticks,
            "failing": self.failing,
        }


@dataclass
class SourceRuntime:
    name: str
    queue: deque[Unit] = field(default_factory=deque)
    checks_queue: deque[Unit] = field(default_factory=deque)
    running: Running | None = None
    checks_running: Running | None = None
    repairs: list[Repair] = field(default_factory=list)
    repair_retry_at: float = 0.0
    state: str = "active"
    fingerprint: str | None = None
    since: dt.datetime | None = None
    last_reminder: dt.datetime | None = None
    last_fingerprint_check: dt.datetime | None = None
    cooldown_until: dt.datetime | None = None
    rate_limits: int = 0
    repeatedly: bool = False
    resumed_at: dt.datetime | None = None
    last_success_at: dt.datetime | None = None
    first_run: str | None = None

    def load(self, doc: dict[str, Any]) -> None:
        self.state = doc.get("state") if doc.get("state") == "waiting_for_auth" else "active"
        self.fingerprint = doc.get("fingerprint")
        self.since = parse_time(doc.get("since"))
        self.last_reminder = parse_time(doc.get("last_reminder"))
        self.cooldown_until = parse_time(doc.get("cooldown_until"))
        self.rate_limits = int(doc.get("consecutive_rate_limits") or 0)
        self.repeatedly = bool(doc.get("repeatedly_rate_limited"))
        self.resumed_at = parse_time(doc.get("resumed_at"))
        self.last_success_at = parse_time(doc.get("last_source_success_at"))
        self.first_run = doc.get("first_run")

    def doc(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "since": _iso(self.since),
            "fingerprint": self.fingerprint,
            "last_reminder": _iso(self.last_reminder),
            "cooldown_until": _iso(self.cooldown_until),
            "consecutive_rate_limits": self.rate_limits,
            "repeatedly_rate_limited": self.repeatedly,
            "resumed_at": _iso(self.resumed_at),
            "last_source_success_at": _iso(self.last_success_at),
            "first_run": self.first_run,
        }

    def cooling(self, now: dt.datetime) -> bool:
        return self.cooldown_until is not None and now < self.cooldown_until

    def status(self, now: dt.datetime) -> str:
        if self.state == "waiting_for_auth":
            return "waiting_for_auth"
        return "rate_limited" if self.cooling(now) else "active"


@dataclass
class PauseState:
    active: bool = False
    reason: str | None = None
    errno: str | None = None
    folder: str | None = None
    delay: float = 300.0
    next_retry: dt.datetime | None = None
    last_delay: float | None = None
    resumed_at: dt.datetime | None = None

    def doc(self) -> dict[str, Any] | None:
        if not self.active:
            return None
        return {
            "reason": self.reason,
            "errno": self.errno,
            "folder": self.folder,
            "next_retry": _iso(self.next_retry),
        }
