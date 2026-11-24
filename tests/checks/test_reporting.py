"""Results in `/state/checks/<source>.json` and the events they log
(CHECKS-REPORT-001..007, -009, CHECKS-RUN-007)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from checks_helpers import NOW, Logs, read_state

from wearstream import log
from wearstream.checks import Reporter, Result
from wearstream.state import StateStore, iso

NAME = "whoop_sleep_schema_drift"
DRIFT = Result("error", "drift", {"new_keys": ["signature"]}, action="accept it")
LATER = NOW + dt.timedelta(hours=1)
NEXT_DAY = NOW + dt.timedelta(hours=24)


@pytest.fixture
def reporter(state: Path, logs: Logs) -> Reporter:
    return Reporter(StateStore(state), "whoop", log.get_logger("checks"))


def record(reporter: Reporter, result: Result, now: dt.datetime = NOW) -> None:
    reporter.record(NAME, result, now, collection="sleep", family="schema_drift")


def row(state: Path, name: str = NAME) -> dict:
    return read_state(state, "checks/whoop.json")["checks"][name]


# @spec CHECKS-REPORT-001, CHECKS-REPORT-002
def test_a_first_failure_is_logged_and_stored(reporter: Reporter, state: Path, logs: Logs) -> None:
    record(reporter, DRIFT)
    event = logs.events("check_failed")[0]
    assert (event["level"], event["source"], event["collection"]) == ("error", "whoop", "sleep")
    assert (event["family"], event["status"], event["action"]) == (
        "schema_drift",
        "drift",
        "accept it",
    )
    assert event["details"] == {"new_keys": ["signature"]}
    stored = row(state)
    assert (stored["severity"], stored["status"]) == ("error", "drift")
    assert stored["since"] == stored["last_logged"] == iso(NOW)
    assert stored["details"] == {"new_keys": ["signature"]}
    assert stored["suppressed"] is None


# @spec CHECKS-REPORT-002
def test_a_warning_logs_check_warning(reporter: Reporter, logs: Logs) -> None:
    record(reporter, Result("warning", "missing", {"missing_keys": ["a"]}))
    assert logs.events("check_warning")[0]["level"] == "warning"


# @spec CHECKS-REPORT-002, CHECKS-REPORT-003
def test_the_same_failure_is_reminded_about_once_a_day(
    reporter: Reporter, state: Path, logs: Logs
) -> None:
    record(reporter, DRIFT)
    record(reporter, DRIFT, LATER)
    assert len(logs.events("check_failed")) == 1
    record(reporter, DRIFT, NEXT_DAY)
    reminders = logs.events("check_failed")
    assert len(reminders) == 2 and reminders[1]["since"] == iso(NOW)
    assert row(state)["last_logged"] == iso(NEXT_DAY)


# @spec CHECKS-REPORT-002
@pytest.mark.parametrize(
    "changed",
    [
        Result("error", "drift", {"new_keys": ["other"]}, action="x"),
        Result("error", "baseline_unreadable", {}, action="x"),
        Result("warning", "missing", {"missing_keys": ["a"]}),
    ],
)
def test_a_change_of_severity_status_or_details_is_logged_at_once(
    reporter: Reporter, state: Path, logs: Logs, changed: Result
) -> None:
    record(reporter, DRIFT)
    record(reporter, changed, LATER)
    assert len(logs.events("check_failed") + logs.events("check_warning")) == 2
    assert row(state)["since"] == iso(LATER)


# @spec CHECKS-REPORT-004
def test_recovery_is_logged_and_a_first_pass_is_not(
    reporter: Reporter, state: Path, logs: Logs
) -> None:
    record(reporter, Result("pass", "ok"))
    assert logs.names() == []
    record(reporter, DRIFT, LATER)
    record(reporter, Result("pass", "ok"), NEXT_DAY)
    assert logs.events("check_recovered")[0]["level"] == "info"
    assert row(state)["severity"] == "pass"
    record(reporter, Result("pass", "ok"), NEXT_DAY)
    assert len(logs.events("check_recovered")) == 1


# @spec CHECKS-REPORT-005
def test_suppression_keeps_the_stored_result_beneath_a_flag(
    reporter: Reporter, state: Path, logs: Logs
) -> None:
    record(reporter, DRIFT)
    quiet = Result("error", "stale", {}, action="x", suppressed="rate_limited")
    record(reporter, quiet, LATER)
    record(reporter, quiet, NEXT_DAY)
    assert len(logs.events("check_suppressed")) == 1
    stored = row(state)
    assert (stored["severity"], stored["status"], stored["suppressed"]) == (
        "error",
        "drift",
        "rate_limited",
    )
    assert stored["since"] == stored["last_logged"] == iso(NOW)
    record(reporter, Result("pass", "ok"), NEXT_DAY + dt.timedelta(hours=1))
    assert logs.names()[-2:] == ["check_unsuppressed", "check_recovered"]
    assert row(state)["suppressed"] is None


# @spec CHECKS-REPORT-005
def test_a_failure_that_outlasts_its_suppression_is_not_reported_again_early(
    reporter: Reporter, logs: Logs
) -> None:
    record(reporter, DRIFT)
    record(reporter, Result("error", "x", {}, action="x", suppressed="resuming"), LATER)
    record(reporter, DRIFT, LATER + dt.timedelta(hours=1))
    assert len(logs.events("check_failed")) == 1


# @spec CHECKS-REPORT-006
def test_collection_checks_no_longer_configured_are_removed(
    reporter: Reporter, state: Path, logs: Logs
) -> None:
    record(reporter, DRIFT)
    reporter.record("whoop_token_health", Result("pass", "ok"), NOW)
    reporter.remove_absent(set())
    assert list(read_state(state, "checks/whoop.json")["checks"]) == ["whoop_token_health"]
    event = logs.events("check_removed")[0]
    assert (event["level"], event["check"]) == ("info", NAME)


# @spec CHECKS-REPORT-007
def test_the_event_is_logged_before_the_result_is_saved(
    reporter: Reporter, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(StateStore, "write", refuse)
    with pytest.raises(OSError):
        record(reporter, DRIFT)
    assert logs.names() == ["check_failed"]


# @spec CHECKS-REPORT-009
def test_a_hand_started_run_logs_but_leaves_the_last_logged_time(state: Path, logs: Logs) -> None:
    manual = Reporter(StateStore(state), "whoop", log.get_logger("checks"), manual=True)
    record(manual, DRIFT)
    assert row(state)["last_logged"] is None
    scheduled = Reporter(StateStore(state), "whoop", log.get_logger("checks"))
    record(scheduled, DRIFT, LATER)
    assert len(logs.events("check_failed")) == 2
    assert row(state)["last_logged"] == iso(LATER)


# @spec CHECKS-RUN-007
def test_a_sources_own_check_is_named_in_place_of_collection_and_family(
    reporter: Reporter, logs: Logs
) -> None:
    reporter.record("whoop_token_health", Result("error", "expired", {}, action="re-auth"), NOW)
    event = logs.events("check_failed")[0]
    assert event["check"] == "whoop_token_health"
    assert "collection" not in event and "family" not in event
