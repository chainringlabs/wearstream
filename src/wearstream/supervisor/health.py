"""`wearstream healthcheck`: healthy when the heartbeat is fresh and capture is not paused."""

from __future__ import annotations

import datetime as dt
import os

from wearstream.settings import ensure_valid, load, paths
from wearstream.state import StateStore, parse_time

STALE_SECONDS = 120


# @spec SUPERVISOR-HEALTH-002
def healthcheck() -> int:
    """Read only `/state`; exit 1 (never 2, which Docker reserves) when unhealthy."""
    loaded = load(os.environ, "healthcheck")
    ensure_valid(loaded, "healthcheck")
    beat = StateStore(paths(loaded.values).state).read_quiet("heartbeat.json")
    updated = parse_time((beat or {}).get("updated_at"))
    if beat is None or updated is None:
        print("unhealthy: no readable heartbeat in /state")
        return 1
    age = (dt.datetime.now(dt.UTC) - updated).total_seconds()
    if age > STALE_SECONDS:
        print(f"unhealthy: the heartbeat is {age:.0f} seconds old")
        return 1
    if beat.get("pause"):
        print(f"unhealthy: capture is paused: {beat['pause'].get('reason')}")
        return 1
    print("healthy")
    return 0
