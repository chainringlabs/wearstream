"""The `needs_refill` set in `/state/backfill/<source>.<job>.json`, shared by the daemon and
hand-started runs."""

from __future__ import annotations

from typing import Any

from wearstream.state.store import StateStore


def backfill_doc(source: str, job: str) -> str:
    return f"backfill/{source}.{job}.json"


# @spec SUPERVISOR-AFTER-006
def record_refill(
    store: StateStore, source: str, job: str, day: str, succeeded: bool
) -> dict[str, Any] | None:
    """Add a day that did not succeed to the job's refill set; remove one that did."""

    def change(doc: dict[str, Any] | None) -> dict[str, Any]:
        doc = dict(doc or {})
        days = set(doc.get("needs_refill", []))
        if succeeded:
            days.discard(day)
        else:
            days.add(day)
        doc["needs_refill"] = sorted(days)
        return doc

    return store.update(backfill_doc(source, job), change)
