"""A source's `checks` unit: run each check under its limit, write what it calls for, report
(checks design, *Running the checks*)."""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wearstream.checks.completeness import completeness
from wearstream.checks.content import content_health
from wearstream.checks.freshness import freshness
from wearstream.checks.report import Reporter
from wearstream.checks.result import OwnCheck, Result
from wearstream.checks.schema import baseline_path, schema_drift
from wearstream.checks.settings import FAMILY_NAMES, CheckSetting, check_name
from wearstream.checks.suppress import suppression_reason
from wearstream.state import StateStore

SOFT_LIMIT_SECONDS = 60.0  # a check running longer is abandoned
MIN_REMAINING_SECONDS = 90.0  # no check starts with less time than this before the deadline
REPORT_ACTION = "Report this problem to WearStream, with the logged error_type."

Family = Callable[[Path, Path, CheckSetting, dt.datetime, str | None], Result]
FAMILIES: dict[str, Family] = {
    "freshness": lambda data, _state, s, now, why: freshness(data, s, now, why),
    "completeness": lambda data, _state, s, now, why: completeness(data, s, now, why),
    "schema": lambda data, state, s, _now, _why: schema_drift(data, state, s),
    "content": lambda data, _state, s, _now, _why: content_health(data, s),
}
SEVERITY = {
    "freshness": "error",
    "completeness": "warning",
    "schema": "error",
    "content": "warning",
}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True)
class _Planned:
    name: str
    call: Callable[[], Result]
    severity: str  # when the check raises or runs too long
    setting: CheckSetting | None = None
    family: str | None = None


def _plan(ctx: Any, settings: list[CheckSetting], own: list[OwnCheck], why: str | None) -> list:
    data, state, now = ctx.paths.data, ctx.paths.state, utc_now()
    planned = [_Planned(o.name, lambda o=o: o.run(why), "error") for o in own]
    for s in settings:
        for family in s.families:
            run = FAMILIES[family]
            planned.append(
                _Planned(
                    check_name(s, family),
                    lambda run=run, s=s: run(data, state, s, now, why),
                    SEVERITY[family],
                    s,
                    family,
                )
            )
    return planned


# @spec CHECKS-RUN-002, CHECKS-RUN-003
def bounded(call: Callable[[], Result], severity: str) -> Result:
    """Run one check in a thread; one that raises or outlives the soft limit gets a result at
    its family's severity, and anything it returns later is ignored."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = call()
        except Exception as exc:
            box["error"] = type(exc).__name__

    worker = threading.Thread(target=target, name="check", daemon=True)
    worker.start()
    worker.join(SOFT_LIMIT_SECONDS)
    if worker.is_alive():
        return Result(severity, "check_timed_out", {"error_type": "Timeout"}, REPORT_ACTION)
    if "error" in box:
        return Result(severity, "check_error", {"error_type": box["error"]}, REPORT_ACTION)
    return box["result"]


def _tally(counts: dict[str, int], result: Result) -> None:
    if result.suppressed:
        counts["suppressed"] += 1
    else:
        counts[{"pass": "passed", "warning": "warned"}.get(result.severity, "failed")] += 1


def _write_baseline(store: StateStore, item: _Planned, result: Result, logger: Any) -> None:
    s = item.setting
    if s is None or result.baseline is None:
        return
    path = baseline_path(store.root, s.source, s.collection)
    store.write(str(path.relative_to(store.root)), result.baseline)
    logger.info(
        "schema_baseline_set",
        source=s.source,
        collection=s.collection,
        keys=result.baseline["keys"],
    )


# @spec CHECKS-RUN-001, CHECKS-RUN-004, CHECKS-RUN-005, CHECKS-RUN-006, CHECKS-REPORT-008
# @spec CHECKS-SUPP-004
def run_unit(ctx: Any, settings: list[CheckSetting], own: list[OwnCheck]) -> None:
    """The source's own checks first, then each collection check; results saved one by one."""
    deadline = time.monotonic() + ctx.job.time_limit
    logger = ctx.log.bind(component="checks")
    store = StateStore(ctx.paths.state)
    reporter = Reporter(store, ctx.source, logger, manual=ctx.trigger == "manual")
    reporter.remove_absent({check_name(s, f) for s in settings for f in s.families})
    why = suppression_reason(ctx.paths.state, ctx.source, utc_now())
    planned = _plan(ctx, settings, own, why)
    counts = {"passed": 0, "warned": 0, "failed": 0, "suppressed": 0}
    for index, item in enumerate(planned):
        if deadline - time.monotonic() < MIN_REMAINING_SECONDS:
            logger.warning(
                "checks_skipped",
                checks=[p.name for p in planned[index:]],
                reason="too little time left before the unit's time limit; they run next time",
            )
            break
        result = bounded(item.call, item.severity)
        _write_baseline(store, item, result, logger)
        collection = item.setting.collection if item.setting else None
        family = FAMILY_NAMES[item.family] if item.family else None
        reporter.record(item.name, result, utc_now(), collection=collection, family=family)
        _tally(counts, result)
    logger.info("checks_finished", **counts)
