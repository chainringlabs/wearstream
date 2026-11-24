"""Freshness: are new captures still landing (checks design, *Freshness*)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from wearstream.checks.payloads import trailing
from wearstream.checks.result import Result
from wearstream.checks.settings import CheckSetting
from wearstream.state import iso

STALE_ACTION = (
    "Check this source's recent run_failed and run_killed lines; if your devices don't produce "
    "this collection, deselect it."
)
EXPECTED_EMPTY_HINT = "If your devices no longer produce this collection, deselect it."
SUPPRESSION_LIMIT = 3  # tolerances after which a stale collection is never suppressed


# @spec CHECKS-FRESH-001, CHECKS-FRESH-002, CHECKS-FRESH-003, CHECKS-SUPP-001, CHECKS-SUPP-002
def freshness(
    data: Path, s: CheckSetting, now: dt.datetime, suppressed: str | None = None
) -> Result:
    """Fail when the newest capture in the trailing folders is older than the tolerance."""
    captures = [c for _day, found in trailing(data, s) for c in found]
    if not captures:
        if s.expected_empty:
            return Result("pass", "never_captured")
        return Result("error", "never_captured", {}, STALE_ACTION, suppressed)
    newest_ms = max(c.ms for c in captures)
    newest = dt.datetime.fromtimestamp(newest_ms / 1000, dt.UTC)
    age = (now - newest).total_seconds() / 3600
    details = {"newest": iso(newest), "age_hours": round(age, 1)}  # neither is a key detail
    if age <= s.tolerance_hours:
        return Result("pass", "ok", details)
    if age > SUPPRESSION_LIMIT * s.tolerance_hours:
        suppressed = None
    if s.expected_empty:
        return Result("warning", "stale", details, EXPECTED_EMPTY_HINT, suppressed)
    return Result("error", "stale", details, STALE_ACTION, suppressed)
