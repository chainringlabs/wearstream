"""Whether capture can happen for a source right now, as `/state` records it
(checks design, *Suppression*)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from wearstream.state import StateStore, parse_time

HEARTBEAT_FRESH_SECONDS = 120


# @spec CHECKS-SUPP-001
def suppression_reason(state: Path, source: str, now: dt.datetime) -> str | None:
    """`capture_paused`, `waiting_for_auth`, `rate_limited` or `resuming` while a running daemon
    says capture can't happen (or hasn't succeeded since it resumed); otherwise None."""
    store = StateStore(state)
    beat = store.read_quiet("heartbeat.json") or {}
    updated = parse_time(beat.get("updated_at"))
    if updated is None or (now - updated).total_seconds() > HEARTBEAT_FRESH_SECONDS:
        return None
    if beat.get("pause"):
        return "capture_paused"
    doc = store.read_quiet(f"sources/{source}.json") or {}
    if doc.get("state") == "waiting_for_auth":
        return "waiting_for_auth"
    until = parse_time(doc.get("cooldown_until"))
    if until is not None and until > now:
        return "rate_limited"
    resumed = parse_time(doc.get("resumed_at"))
    succeeded = parse_time(doc.get("last_source_success_at"))
    if resumed is not None and (succeeded is None or succeeded <= resumed):
        return "resuming"
    return None
