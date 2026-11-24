"""`wearstream accept-schema`: accept a collection's current payload shape
(checks design, *Accepting a schema change*)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from wearstream import jobs, log
from wearstream.checks.schema import baseline_doc, baseline_path, current_keys
from wearstream.checks.settings import CheckSetting, check_name
from wearstream.runner import BUSY, OK, USAGE
from wearstream.settings import ensure_valid, load, paths
from wearstream.state import StateStore, release, wait_lock

EXTRA_WAIT_SECONDS = 120.0  # beyond the checks time limit, so a running unit can finish first
ACCEPTABLE = ("drift", "baseline_unreadable")


def _schema_settings(source: str, values: dict[str, Any]) -> dict[str, CheckSetting] | None:
    from wearstream.sources import get_source

    try:
        declared = get_source(source).check_settings(values)  # type: ignore[attr-defined]
    except LookupError:
        return None
    return {s.collection: s for s in declared if "schema" in s.families}


def _accept(store: StateStore, data: Path, s: CheckSetting, logger: Any) -> bool:
    keys = current_keys(data, s)
    if keys is None:
        print(
            f"{s.source} {s.collection}: no readable, non-empty payload in its recent folders "
            "to accept; nothing changed."
        )
        return False
    path = baseline_path(store.root, s.source, s.collection)
    store.write(str(path.relative_to(store.root)), baseline_doc(keys))
    name = check_name(s, "schema")

    def mark(doc: dict | None) -> dict:
        rows = dict((doc or {}).get("checks") or {})
        if name in rows:
            rows[name] = {**rows[name], "accepted": True}
        return {**(doc or {}), "checks": rows}

    store.update(f"checks/{s.source}.json", mark)
    logger.info("schema_baseline_set", source=s.source, collection=s.collection, keys=sorted(keys))
    print(f"Accepted the current {s.source} {s.collection} schema ({len(keys)} keys).")
    return True


def _targets(store: StateStore, source: str, chosen: dict[str, CheckSetting]) -> list[str]:
    rows = (store.read_quiet(f"checks/{source}.json") or {}).get("checks") or {}
    return [
        c
        for c, s in chosen.items()
        if (rows.get(check_name(s, "schema")) or {}).get("status") in ACCEPTABLE
    ]


# @spec CHECKS-ACCEPT-001, CHECKS-ACCEPT-002, CHECKS-ACCEPT-003
def accept_schema(source: str, collection: str | None, every: bool) -> int:
    """Record the current keys as the baseline, under the source's checks lock."""
    loaded = load(os.environ, "accept-schema", source)
    ensure_valid(loaded, "accept-schema")
    values = loaded.values
    log.configure(fmt=values["WEARSTREAM_LOG_FORMAT"], level=values["WEARSTREAM_LOG_LEVEL"])
    chosen = _schema_settings(source, values) if source in jobs.DECLARATIONS else None
    if chosen is None:
        print(f"{source} is not a WearStream source in this build (use whoop or garmin).")
        return USAGE
    if not every and collection not in chosen:
        print(
            f"{collection} is not a selected {source} collection with a schema check; "
            "nothing changed."
        )
        return USAGE
    resolved = paths(values)
    limit = values[jobs.setting(source, "checks", "TIME_LIMIT")] * jobs.SECONDS_PER_MINUTE
    fd = wait_lock(
        resolved.state_path("locks", f"{source}.checks.lock"), limit + EXTRA_WAIT_SECONDS
    )
    if fd is None:
        print(f"A {source} checks run still holds its lock; try again in a few minutes.")
        return BUSY
    try:
        store, logger = StateStore(resolved.state), log.get_logger("checks")
        names = _targets(store, source, chosen) if every else [collection or ""]
        if every and not names:
            print(f"No {source} collection has a schema change waiting to be accepted.")
        accepted = [c for c in names if _accept(store, resolved.data, chosen[c], logger)]
        return OK if every or accepted else USAGE
    finally:
        release(fd)
