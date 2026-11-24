"""Credential-safe logging and the library's own loggers (GARMIN-AUTH-LOG-001..003,
GARMIN-CAPTURE-REPORT-001..003)."""

from __future__ import annotations

import logging
import os
import signal
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from garmin_helpers import PASSWORD, FakeLibrary, Logs
from garminconnect import GarminConnectConnectionError

from wearstream.runner.run import execute

Run = Callable[..., tuple[int, dict[str, Any]]]


@pytest.fixture
def restore_signals() -> Iterator[None]:
    saved = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, saved)


# @spec GARMIN-AUTH-LOG-002
def test_failures_log_only_their_type(
    session: Path, credentials: None, garmin: FakeLibrary, run: Run, logs: Logs
) -> None:
    garmin.resume = [GarminConnectConnectionError(f"token=DI-TOKEN-1 password={PASSWORD}")]
    garmin.answers["get_floors"] = RuntimeError("body with DI-TOKEN-1")
    run()
    failures = [e for e in logs.events() if e.get("error_type")]
    assert failures
    for event in failures:
        assert "traceback" not in event and "error" not in event
    assert "DI-TOKEN-1" not in logs.text() and PASSWORD not in logs.text()


# @spec GARMIN-AUTH-LOG-003
@pytest.mark.parametrize("debug", ["false", "true"])
def test_the_library_loggers_are_silent_unless_debugging(
    session: Path, garmin: FakeLibrary, run: Run, debug: str
) -> None:
    run(WEARSTREAM_GARMIN_LIBRARY_DEBUG=debug)
    library = logging.getLogger("garminconnect")
    if debug == "false":
        assert library.level > logging.CRITICAL and library.propagate is False
    else:
        assert library.level == logging.DEBUG


# @spec GARMIN-CAPTURE-REPORT-001
def test_a_failing_call_is_a_failed_request_and_the_unit_goes_on(
    session: Path, garmin: FakeLibrary, run: Run
) -> None:
    garmin.answers["get_floors"] = GarminConnectConnectionError("500")
    code, result = run()
    assert code == 1 and "floors" in result["error"]
    assert garmin.methods()[-1] == "get_activities_by_date"


# @spec GARMIN-CAPTURE-REPORT-002
def test_no_call_starts_in_the_last_three_minutes(
    session: Path, garmin: FakeLibrary, run: Run, logs: Logs
) -> None:
    garmin.per_call_seconds = 100
    code, result = run(time_limit=900)
    assert code == 1
    starts = [t for *_rest, t in garmin.calls]
    assert starts and max(starts) <= 900 - 180
    [event] = logs.events("garmin_unit_out_of_time")
    assert "activities" in event["collections"]


# @spec GARMIN-CAPTURE-REPORT-003
def test_a_stop_ends_the_unit_at_once(
    session: Path, garmin: FakeLibrary, unit: Callable[..., Any], restore_signals: None
) -> None:
    def stop(*_args: Any) -> dict[str, Any]:
        os.kill(os.getpid(), signal.SIGTERM)
        return {"floors": 1}

    garmin.answers["get_floors"] = stop
    ctx = unit()
    ctx.signals.install()
    assert execute(ctx, ctx.log) == 75
    assert garmin.methods()[-1] == "get_floors"
    assert ctx.result()["error"] is None
