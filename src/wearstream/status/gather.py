"""What `wearstream status` reports, read from `/state` alone (operator design, *status*)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from wearstream.jobs import DECLARATIONS
from wearstream.state import StateStore, iso, parse_time

SCHEMA_VERSION = 1
HEARTBEAT_FRESH_SECONDS = 120
_STATE_ENTRIES = (
    "heartbeat.json",
    "inflight.json",
    "sources",
    "jobs",
    "backfill",
    "checks",
    "runs",
)
ACTIONS = {
    "daemon_not_running": "Start the container, or read the end of its log to see why the "
    "daemon stopped.",
    "capture_paused": "Fix what the daemon's capture_paused line names; capture resumes on its "
    "own.",
    "repeatedly_rate_limited": "The vendor keeps rate-limiting this source: run its jobs less "
    "often or lower their windows. WearStream keeps backing off meanwhile.",
    "job_failing": "Read this job's recent run_failed lines: they say which requests failed "
    "and why.",
    "unreadable_document": "WearStream treats the document as absent and rewrites it; if this "
    "keeps happening, check the filesystem that holds /state.",
}


class _Reader:
    """Reads `/state` documents, remembering the ones that exist but can't be read."""

    def __init__(self, state: Path) -> None:
        self.store = StateStore(state)
        self.unreadable: list[str] = []

    def doc(self, relative: str) -> dict[str, Any] | None:
        found = self.store.read_quiet(relative)
        if found is None and self.store.path(relative).exists():
            self.unreadable.append(relative)
        return found


def _daemon(beat: dict[str, Any] | None, unreadable: bool, now: dt.datetime) -> dict[str, Any]:
    updated = parse_time((beat or {}).get("updated_at"))
    age = (now - updated).total_seconds() if updated else None
    running = age is not None and age <= HEARTBEAT_FRESH_SECONDS
    if running:
        heartbeat = "fresh"
    else:
        heartbeat = "unreadable" if unreadable else "missing" if beat is None else "stale"
    return {
        "running": running,
        "heartbeat": heartbeat,
        "heartbeat_age_seconds": round(age) if age is not None else None,
        "phase": (beat or {}).get("phase"),
        "version": (beat or {}).get("version"),
        "pause": (beat or {}).get("pause") if running else None,
    }


def _enabled(reader: _Reader, beat: dict[str, Any] | None) -> list[str]:
    listed = (beat or {}).get("sources")
    if isinstance(listed, dict | list) and listed:
        return [str(name) for name in listed]
    return sorted(
        name for name in DECLARATIONS if reader.store.path(f"sources/{name}.json").exists()
    )


def _source_state(doc: dict[str, Any], now: dt.datetime) -> str:
    if doc.get("state") == "waiting_for_auth":
        return "waiting_for_auth"
    until = parse_time(doc.get("cooldown_until"))
    return "rate_limited" if until and until > now else "active"


def _backfill(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "in_progress": bool(doc.get("in_progress")),
        "position": doc.get("position"),
        "counts": doc.get("counts") or {},
        "needs_refill": len(doc.get("needs_refill") or []),
        "known_empty": len(doc.get("known_empty") or {}),
    }


def _source(reader: _Reader, name: str, units: list, now: dt.datetime) -> dict[str, Any]:
    doc = reader.doc(f"sources/{name}.json") or {}
    jobs, backfill = {}, {}
    for job, declaration in DECLARATIONS.get(name, {}).items():
        jobs[job] = reader.doc(f"jobs/{name}.{job}.json") or {}
        if declaration.kind == "partitioned":
            found = reader.doc(f"backfill/{name}.{job}.json")
            if found is not None:
                backfill[job] = _backfill(found)
    return {
        "state": _source_state(doc, now),
        "since": doc.get("since"),
        "cooldown_until": doc.get("cooldown_until"),
        "repeatedly_rate_limited": bool(doc.get("repeatedly_rate_limited")),
        "last_source_success_at": doc.get("last_source_success_at"),
        "jobs": {
            job: {
                "last_completed_tick": d.get("last_completed_tick"),
                "last_tick_outcome": d.get("last_tick_outcome"),
                "failing": bool(d.get("failing")),
            }
            for job, d in jobs.items()
        },
        "in_flight": [u for u in units if isinstance(u, dict) and u.get("source") == name],
        "backfill": backfill,
    }


def _checks(reader: _Reader, name: str) -> list[dict[str, Any]]:
    rows = (reader.doc(f"checks/{name}.json") or {}).get("checks") or {}
    shown = []
    for check, row in sorted(rows.items()):
        if row.get("severity") in ("warning", "error") or row.get("suppressed"):
            shown.append(
                {
                    "source": name,
                    "check": check,
                    **{k: row.get(k) for k in ("severity", "status", "since", "suppressed")},
                    "accepted": bool(row.get("accepted")),
                    "action": row.get("action"),
                }
            )
    return shown


def never_ran(state: Path) -> bool:
    return not any((Path(state) / entry).exists() for entry in _STATE_ENTRIES)


def gather(state: Path, now: dt.datetime) -> dict[str, Any]:
    """The report, before "needs attention" is derived from it."""
    reader = _Reader(state)
    beat = reader.doc("heartbeat.json")
    daemon = _daemon(beat, "heartbeat.json" in reader.unreadable, now)
    units = (reader.doc("inflight.json") or {}).get("units") or []
    names = _enabled(reader, beat)
    sources = {name: _source(reader, name, units, now) for name in names}
    checks = [row for name in names for row in _checks(reader, name)]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now),
        "daemon": daemon,
        "sources": sources,
        "checks": checks,
        "unreadable": reader.unreadable,
    }
