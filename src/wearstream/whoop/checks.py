"""Whoop's collection check settings and its token-health check
(Whoop capture design, *Collection checks*; Whoop auth design, *Token health check*)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from wearstream.checks import CheckSetting, OwnCheck, Result
from wearstream.cron import Cron
from wearstream.state import parse_time
from wearstream.whoop import clock
from wearstream.whoop.files import TokenStore, TokenUnreadable

DAILY = (("sleep", "start"), ("recovery", "created_at"), ("cycle", "start"))
EVERY = ("freshness", "completeness", "schema", "content")
HEALTH_MARGIN = dt.timedelta(minutes=30)
HEALTH_ACTION = (
    "Run `wearstream auth whoop`: refreshes have stopped succeeding or stopped happening."
)


# @spec WHOOP-CAPTURE-CHECKS-001, WHOOP-CAPTURE-CHECKS-002, WHOOP-CAPTURE-CHECKS-003
# @spec WHOOP-CAPTURE-CHECKS-005, WHOOP-CAPTURE-CHECKS-006
def check_settings(_values: dict[str, Any]) -> list[CheckSetting]:
    """Range collections are page wrappers (their records are checked); snapshots are objects."""
    by_payload = {"event_date_source": "payload", "unnest_records": True}
    found = [
        CheckSetting("whoop", name, EVERY, event_date_field=field, **by_payload)  # type: ignore[arg-type]
        for name, field in DAILY
    ]
    found.insert(
        2,
        CheckSetting(
            "whoop",
            "workout",
            ("completeness", "schema"),
            event_date_field="start",
            cadence_days=14,
            gap_to_now=True,
            **by_payload,  # type: ignore[arg-type]
        ),
    )
    found += [
        CheckSetting("whoop", name, ("freshness", "schema", "content"))
        for name in ("profile", "body_measurement")
    ]
    return found


def _failed(status: str, suppressed: str | None, **details: Any) -> Result:
    return Result("error", status, details, HEALTH_ACTION, suppressed)


# @spec WHOOP-AUTH-HEALTH-001, WHOOP-AUTH-HEALTH-002, WHOOP-AUTH-HEALTH-003
def token_health(values: dict[str, Any], paths: Any) -> OwnCheck:
    """Reads only the token file: fails when it is missing, unreadable, or expired for longer
    than the `range` job's interval plus 30 minutes; suppressed while capture can't happen."""
    limit = Cron(values["WEARSTREAM_WHOOP_SCHEDULE"]).interval + HEALTH_MARGIN
    store = TokenStore(paths.secrets_path("whoop"))

    def run(suppressed: str | None) -> Result:
        try:
            expires = parse_time(store.read()["expires_at"])
        except FileNotFoundError:
            return _failed("missing", suppressed)
        except TokenUnreadable:
            return _failed("unreadable", suppressed)
        if expires is None:
            return _failed("unreadable", suppressed)
        overdue = clock.utc_now() - expires
        if overdue > limit:
            return _failed("stale", suppressed, expired_minutes=int(overdue.total_seconds() // 60))
        return Result("pass", "ok")

    return OwnCheck("whoop_token_health", run)
