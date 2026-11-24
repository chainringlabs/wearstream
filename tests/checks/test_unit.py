"""A checks unit: order, limits, failures, writes and the finish line
(CHECKS-RUN-001..007, CHECKS-SET-003, CHECKS-SCHEMA-003, CHECKS-SUPP-004, CHECKS-REPORT-006,
-008, -009)."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from checks_helpers import Logs, heartbeat, hours_ago, put, read_state, setting, write_state

import wearstream.checks.unit as unit
from wearstream.checks import OwnCheck, Result, run_unit
from wearstream.runner.run import execute

Ctx = Callable[..., Any]


@pytest.fixture
def release() -> Iterator[threading.Event]:
    """Lets a deliberately hung check finish when the test ends."""
    event = threading.Event()
    yield event
    event.set()


def rows(state: Path) -> dict[str, Any]:
    return read_state(state, "checks/whoop.json")["checks"]


def own(name: str, result: Result | Callable[[str | None], Result]) -> OwnCheck:
    return OwnCheck(name, result if callable(result) else (lambda _suppressed: result))


# @spec CHECKS-RUN-001, CHECKS-RUN-004
def test_own_checks_run_first_and_each_result_is_saved_at_once(
    unit_ctx: Ctx, state: Path, data: Path
) -> None:
    seen: list[set[str]] = []

    def second(_suppressed: str | None) -> Result:
        seen.append(set(rows(state)))
        return Result("pass", "ok")

    put(data, "sleep", "2026-11-24", {"a": 1}, at=hours_ago(1))
    run_unit(
        unit_ctx(),
        [setting("sleep", "freshness")],
        [own("whoop_token_health", Result("pass", "ok")), own("whoop_second", second)],
    )
    assert seen == [{"whoop_token_health"}]
    assert set(rows(state)) == {"whoop_token_health", "whoop_second", "whoop_sleep_freshness"}


# @spec CHECKS-RUN-003
def test_a_check_that_raises_gets_its_familys_severity_and_the_rest_still_run(
    unit_ctx: Ctx, state: Path, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any) -> Result:
        raise ValueError("bad payload")

    monkeypatch.setitem(unit.FAMILIES, "completeness", broken)

    def raising(_suppressed: str | None) -> Result:
        raise KeyError("s3cr3t")

    run_unit(
        unit_ctx(),
        [setting("sleep", "completeness", "freshness", event_date_source="partition")],
        [own("whoop_token_health", raising)],
    )
    stored = rows(state)
    assert stored["whoop_token_health"]["severity"] == "error"
    assert stored["whoop_sleep_completeness"]["severity"] == "warning"
    assert stored["whoop_sleep_freshness"]["severity"] == "error"  # never captured: still ran
    failed = logs.events("check_failed")[0]
    assert failed["error_type"] == "KeyError" and "report" in failed["action"].lower()
    assert "s3cr3t" not in str(failed)
    assert logs.events("check_warning")[0]["error_type"] == "ValueError"


# @spec CHECKS-RUN-002, CHECKS-RUN-005
def test_a_hung_check_is_abandoned_and_writes_nothing(
    unit_ctx: Ctx,
    state: Path,
    release: threading.Event,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(unit, "SOFT_LIMIT_SECONDS", 0.2)
    finished = threading.Event()

    def hung(*_args: Any) -> Result:
        release.wait(10)
        finished.set()
        return Result("pass", "baseline_set", baseline={"version": 1, "keys": ["a"]})

    monkeypatch.setitem(unit.FAMILIES, "schema", hung)
    run_unit(unit_ctx(), [setting("sleep", "schema", "freshness")], [])
    stored = rows(state)
    assert stored["whoop_sleep_schema_drift"]["severity"] == "error"
    assert "whoop_sleep_freshness" in stored
    release.set()
    assert finished.wait(5)
    time.sleep(0.05)
    assert not (state / "checks" / "baselines").exists()


# @spec CHECKS-RUN-006
def test_no_check_starts_too_close_to_the_deadline(
    unit_ctx: Ctx, state: Path, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(unit, "MIN_REMAINING_SECONDS", 0.5)

    def slow(_suppressed: str | None) -> Result:
        time.sleep(0.6)
        return Result("pass", "ok")

    run_unit(unit_ctx(time_limit=1), [setting("sleep"), setting("hr")], [own("whoop_slow", slow)])
    assert set(rows(state)) == {"whoop_slow"}
    skipped = logs.events("checks_skipped")[0]
    assert skipped["level"] == "warning"
    assert skipped["checks"] == ["whoop_sleep_freshness", "whoop_hr_freshness"]


# @spec CHECKS-RUN-004
def test_a_checks_unit_exits_0_whatever_it_finds(unit_ctx: Ctx, fake: Any) -> None:
    fake.settings = [setting("sleep", "freshness")]  # never captured: an error
    fake.own = [own("whoop_token_health", Result("error", "expired", {}, action="re-auth"))]
    ctx = unit_ctx()
    assert execute(ctx, ctx.log) == 0


# @spec CHECKS-SCHEMA-003, CHECKS-RUN-005
def test_the_unit_writes_an_accepted_baseline_and_logs_it(
    unit_ctx: Ctx, data: Path, state: Path, logs: Logs
) -> None:
    put(data, "sleep", "2026-11-24", {"a": 1})
    run_unit(unit_ctx(), [setting("sleep", "schema")], [])
    assert read_state(state, "checks/baselines/whoop/sleep.json")["keys"] == ["a"]
    event = logs.events("schema_baseline_set")[0]
    assert (event["level"], event["collection"], event["keys"]) == ("info", "sleep", ["a"])


# @spec CHECKS-SUPP-004
def test_own_checks_learn_whether_capture_can_happen(
    unit_ctx: Ctx, state: Path, logs: Logs
) -> None:
    heartbeat(state, age=0)
    write_state(state, "sources/whoop.json", {"state": "waiting_for_auth"})
    told: list[str | None] = []

    def token(suppressed: str | None) -> Result:
        told.append(suppressed)
        return Result("error", "expired", {}, action="re-auth", suppressed=suppressed)

    run_unit(unit_ctx(), [], [own("whoop_token_health", token)])
    assert told == ["waiting_for_auth"]
    assert logs.names().count("check_suppressed") == 1


# @spec CHECKS-REPORT-006
def test_a_unit_drops_rows_of_collection_checks_no_longer_configured(
    unit_ctx: Ctx, state: Path, logs: Logs
) -> None:
    write_state(
        state,
        "checks/whoop.json",
        {
            "checks": {
                "whoop_hr_freshness": {"kind": "collection", "severity": "pass", "status": "ok"},
                "whoop_old_token": {"kind": "own", "severity": "pass", "status": "ok"},
            }
        },
    )
    run_unit(unit_ctx(), [setting("sleep", expected_empty=True)], [])
    assert set(rows(state)) == {"whoop_old_token", "whoop_sleep_freshness"}
    assert logs.events("check_removed")[0]["check"] == "whoop_hr_freshness"


# @spec CHECKS-REPORT-008
def test_the_unit_ends_with_counts(unit_ctx: Ctx, state: Path, data: Path, logs: Logs) -> None:
    heartbeat(state, age=0)
    write_state(state, "sources/whoop.json", {"state": "waiting_for_auth"})
    put(data, "sleep", "2026-11-20", {"a": 1}, at=hours_ago(90))
    put(data, "hr", "2026-11-24", {}, at=hours_ago(1))
    run_unit(
        unit_ctx(),
        [setting("sleep"), setting("hr", "freshness", "content"), setting("rhr")],
        [],
    )
    finished = logs.events("checks_finished")[-1]
    assert finished["level"] == "info"
    assert (finished["passed"], finished["warned"], finished["failed"]) == (1, 1, 0)
    assert finished["suppressed"] == 2


# @spec CHECKS-REPORT-009
def test_a_hand_started_unit_leaves_last_logged_alone(unit_ctx: Ctx, state: Path) -> None:
    run_unit(unit_ctx(trigger="manual"), [setting("sleep")], [])
    assert rows(state)["whoop_sleep_freshness"]["last_logged"] is None


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# @spec CHECKS-SET-003
def test_checks_never_change_the_capture_tree(unit_ctx: Ctx, data: Path) -> None:
    for day in ("2026-11-20", "2026-11-22", "2026-11-24"):
        put(data, "sleep", day, [{"start": day}])
    (data / "whoop" / "sleep" / "dt=2026-11-24" / ".partial.tmp").write_text("x")
    before = _snapshot(data)
    families = ("freshness", "completeness", "schema", "content")
    run_unit(
        unit_ctx(),
        [setting("sleep", *families, event_date_source="partition", gap_to_now=True)],
        [],
    )
    assert _snapshot(data) == before
