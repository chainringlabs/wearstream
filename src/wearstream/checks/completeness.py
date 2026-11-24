"""Completeness: gaps in the event timeline (checks design, *Completeness*)."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from wearstream.checks.payloads import is_empty, lookup, parse, records, trailing
from wearstream.checks.result import Result
from wearstream.checks.settings import CheckSetting

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_FORMATS = ("%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d")


# @spec CHECKS-COMPLETE-002
def event_date(value: Any) -> dt.date | None:
    """Best effort: ISO first 10 characters (no offset applied), three other formats, or epoch
    seconds as a UTC date; anything else is ignored."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return dt.datetime.fromtimestamp(value, dt.UTC).date()
        except OverflowError, OSError, ValueError:
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _ISO.match(text):
        try:
            return dt.date.fromisoformat(text[:10])
        except ValueError:
            return None
    for fmt in _FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# @spec CHECKS-COMPLETE-001
def event_dates(data: Path, s: CheckSetting) -> tuple[set[dt.date], bool]:
    """The distinct event dates of non-empty captures, and whether there was any capture."""
    dates: set[dt.date] = set()
    folders = trailing(data, s)
    for day, captures in folders:
        for c in captures:
            value = parse(c, s.reader)
            if is_empty(value, s.reader):
                continue
            if s.event_date_source == "partition":
                dates.add(dt.date.fromisoformat(day))
                continue
            for record in records(value, s):
                found = event_date(lookup(record, s.event_date_field or ""))
                if found:
                    dates.add(found)
    return dates, bool(folders)


def _details(ordered: list[dt.date], cadence: int) -> dict[str, Any]:
    steps = list(zip(ordered, ordered[1:], strict=False))
    return {
        "event_days": len(ordered),
        "earliest": ordered[0].isoformat() if ordered else None,
        "latest": ordered[-1].isoformat() if ordered else None,
        "gaps": [[a.isoformat(), b.isoformat()] for a, b in steps if (b - a).days > cadence],
        "biggest_gap_days": max(((b - a).days for a, b in steps), default=0),
    }


# @spec CHECKS-COMPLETE-003, CHECKS-COMPLETE-004, CHECKS-COMPLETE-005, CHECKS-SUPP-003
def completeness(
    data: Path, s: CheckSetting, now: dt.datetime, suppressed: str | None = None
) -> Result:
    """Warn on gaps over `cadence_days` between event dates, and, with `gap_to_now`, to today."""
    dates, any_capture = event_dates(data, s)
    ordered = sorted(dates)
    details = _details(ordered, s.cadence_days)
    if details["gaps"]:
        return Result("warning", "gaps", details)
    if s.gap_to_now and any_capture:
        if not ordered:
            return Result("warning", "no_recent_event", details, suppressed=suppressed)
        behind = (now.date() - ordered[-1]).days
        details["days_since_latest"] = behind
        if behind > s.cadence_days:
            return Result("warning", "gaps", details, suppressed=suppressed)
    if not s.gap_to_now and len(ordered) < 2:
        return Result("pass", "too_few", details)
    return Result("pass", "ok", details)
