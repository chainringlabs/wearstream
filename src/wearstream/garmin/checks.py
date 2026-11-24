"""Garmin's collection check settings and the token-store health check
(Garmin capture design, *Collection checks*; Garmin auth design, *Token-store health check*)."""

from __future__ import annotations

from typing import Any

from wearstream.checks import CheckSetting, OwnCheck, Result
from wearstream.garmin.catalog import CATALOG, selected

IMPORTANT = ("sleep", "stress", "hrv", "respiration", "spo2", "resting_heart_rate", "heart_rates",
             "steps_intraday", "intensity_minutes", "training_status", "training_readiness",
             "max_metrics", "body_battery", "user_summary")  # fmt: skip
DEFAULT_EXPECTED_EMPTY = ("training_readiness", "body_battery_events", "running_tolerance",
                          "goals", "max_metrics", "hrv", "activities")  # fmt: skip


# @spec GARMIN-CAPTURE-CHECKS-006
def expected_empty(values: dict[str, Any]) -> set[str]:
    """The operator's list (or the built-in one), plus every entry flagged to skip no-data days."""
    chosen = values.get("WEARSTREAM_GARMIN_EXPECTED_EMPTY")
    listed = DEFAULT_EXPECTED_EMPTY if chosen is None else chosen
    return set(listed) | {e.name for e in CATALOG if e.keeps_every_capture}


# @spec GARMIN-CAPTURE-CHECKS-001, GARMIN-CAPTURE-CHECKS-002, GARMIN-CAPTURE-CHECKS-003
# @spec GARMIN-CAPTURE-CHECKS-004, GARMIN-CAPTURE-CHECKS-005
def check_settings(values: dict[str, Any]) -> list[CheckSetting]:
    """Per selected catalog collection; none for per-activity collections."""
    empty, found = expected_empty(values), []
    for entry in CATALOG:
        if not selected(entry.name, values):
            continue
        quiet = entry.name in empty
        if entry.name in IMPORTANT:
            found.append(
                CheckSetting(
                    "garmin",
                    entry.name,
                    ("freshness", "completeness", "schema", "content"),
                    event_date_source="partition",
                    gap_to_now=True,
                    cadence_days=14 if quiet else 2,
                    expected_empty=quiet,
                )
            )
        elif quiet:
            found.append(
                CheckSetting("garmin", entry.name, ("freshness", "content"), expected_empty=True)
            )
        else:
            found.append(CheckSetting("garmin", entry.name, ("freshness",)))
    return found


# @spec GARMIN-AUTH-HEALTH-001, GARMIN-AUTH-HEALTH-002, GARMIN-AUTH-HEALTH-003
def tokenstore_health(paths: Any) -> OwnCheck:
    """Reads only the store; never reports its contents. An unusable store always fails."""
    from wearstream.garmin.store import problem_fields, store_problem, token_file_problem

    folder = paths.secrets_path("garmin")

    def run(suppressed: str | None) -> Result:
        problem = store_problem(folder)
        if problem:
            fields = problem_fields(folder, problem)
            return Result(
                "error",
                "tokenstore_unusable",
                {k: fields[k] for k in ("path", "reason")},
                fields["action"],
            )
        problem = token_file_problem(folder)
        if problem:
            fields = problem_fields(folder, problem)
            details = {k: fields[k] for k in ("path", "reason")}
            return Result("error", "token_file_invalid", details, fields["action"], suppressed)
        return Result("pass", "ok")

    return OwnCheck("garmin_tokenstore_health", run)
