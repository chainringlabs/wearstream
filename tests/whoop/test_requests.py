"""Snapshots, data requests, the time limit, stop signals and what a unit reports
(WHOOP-CAPTURE-SNAP-001, -002, -REQ-001..005, -TIME-001, -002, -STOP-001, -REPORT-002, -004)."""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from whoop_helpers import FakeClock, FakeWhoop, reply

import wearstream.whoop.http as http
from wearstream.capture import list_captures
from wearstream.runner.run import execute

Run = Callable[..., tuple[int, dict[str, Any]]]
DAY = "2026-11-20"


@pytest.fixture
def restore_signals() -> Iterator[None]:
    saved = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, saved)


# @spec WHOOP-CAPTURE-SNAP-001
def test_snapshots_capture_profile_then_body_into_the_fetch_day(
    token: dict, whoop: FakeWhoop, run: Run, data: Path, clock: FakeClock
) -> None:
    assert run("snapshots")[0] == 0
    assert whoop.collections_requested() == ["profile", "body_measurement"]
    fetch_day = dt.datetime.now(dt.UTC).date().isoformat()  # the writer's own UTC date
    for collection in ("profile", "body_measurement"):
        assert len(list_captures(data, "whoop", collection, fetch_day)) == 1
    run("snapshots")
    assert len(list_captures(data, "whoop", "profile", fetch_day)) == 1


# @spec WHOOP-CAPTURE-SNAP-002
def test_a_failed_profile_skips_body_measurement(token: dict, whoop: FakeWhoop, run: Run) -> None:
    whoop.script("profile", reply(404))
    assert run("snapshots")[0] == 1
    assert whoop.collections_requested() == ["profile"]


# @spec WHOOP-CAPTURE-REQ-001
def test_data_requests_share_one_client_with_a_30_second_timeout() -> None:
    client = http.data_client()
    try:
        assert str(client.base_url).rstrip("/") == "https://api.prod.whoop.com"
        assert client.timeout.read == 30 and client.timeout.connect == 30
    finally:
        client.close()


# @spec WHOOP-CAPTURE-REQ-002
@pytest.mark.parametrize(("per_minute", "allowed"), [("60", 54), ("1", 1), ("90", 81)])
def test_attempts_are_limited_to_90_percent_of_the_configured_rate(
    token: dict, whoop: FakeWhoop, clock: FakeClock, per_minute: str, allowed: int
) -> None:
    limiter = http.RateLimiter(int(per_minute))
    for _ in range(allowed):
        limiter.wait()
    assert clock.slept == []
    limiter.wait()
    assert clock.slept and 0 < clock.slept[0] <= 60


# @spec WHOOP-CAPTURE-REQ-003
@pytest.mark.parametrize(
    "first",
    [reply(429, retry_after="7"), reply(500), reply(502), reply(503), reply(504),
     httpx.ReadTimeout("x"), httpx.ConnectError("x")],
)  # fmt: skip
def test_transient_failures_are_retried(
    token: dict, whoop: FakeWhoop, clock: FakeClock, run: Run, first: object
) -> None:
    whoop.script("profile", first, whoop.default)
    assert run("snapshots")[0] == 0
    assert len(whoop.data_requests("profile")) == 2
    assert clock.slept == [
        7 if isinstance(first, httpx.Response) and first.status_code == 429 else 2
    ]


# @spec WHOOP-CAPTURE-REQ-003, WHOOP-CAPTURE-REPORT-001
def test_three_attempts_then_failure_with_retry_after_capped(
    token: dict, whoop: FakeWhoop, clock: FakeClock, run: Run
) -> None:
    whoop.script("profile", reply(503, retry_after="600"))
    code, result = run("snapshots", time_limit=3600)
    assert code == 1 and "profile" in result["error"]
    assert len(whoop.data_requests("profile")) == 3
    assert clock.slept == [60, 60]


# @spec WHOOP-CAPTURE-REQ-004
@pytest.mark.parametrize("answer", [reply(400), reply(404), reply(422), httpx.DecodingError("x")])
def test_other_errors_fail_without_retry(
    token: dict, whoop: FakeWhoop, run: Run, answer: object
) -> None:
    whoop.script("profile", answer)
    assert run("snapshots")[0] == 1
    assert len(whoop.data_requests("profile")) == 1


# @spec WHOOP-CAPTURE-REQ-005
def test_a_token_failure_inside_the_retry_is_not_retried(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run
) -> None:
    from whoop_helpers import minutes, write_token

    write_token(folder, clock, refreshed_at=minutes(clock, -45))
    whoop.script("/oauth/oauth2/token", reply(502))
    assert run("snapshots")[0] == 1
    assert len(whoop.token_forms()) == 1


# @spec WHOOP-CAPTURE-REPORT-004
def test_still_rate_limited_after_retries_ends_the_unit(
    token: dict, whoop: FakeWhoop, run: Run
) -> None:
    whoop.script("sleep", reply(429, retry_after="30"))
    code, result = run(day=DAY)
    assert code == 6
    assert (result["rate_limited"], result["retry_after_seconds"]) == (True, 30)
    assert whoop.collections_requested() == ["sleep"]


# @spec WHOOP-CAPTURE-REPORT-002
def test_a_needed_credential_stops_the_unit(token: dict, whoop: FakeWhoop, run: Run) -> None:
    whoop.script("recovery", reply(403))
    assert run(day=DAY)[0] == 4
    assert whoop.collections_requested() == ["sleep", "recovery"]


# @spec WHOOP-CAPTURE-TIME-001, WHOOP-CAPTURE-TIME-002
def test_no_request_starts_that_could_outlast_the_last_minute(
    token: dict, whoop: FakeWhoop, run: Run
) -> None:
    whoop.per_request_seconds = 80  # 0, 80, 160 start; at 240 only 60 s would remain
    code, result = run(day=DAY, time_limit=300)
    assert code == 1
    assert whoop.collections_requested() == ["sleep", "recovery", "workout"]
    assert "cycle" in result["error"]


# @spec WHOOP-CAPTURE-TIME-001
def test_no_wait_starts_that_could_outlast_the_last_minute(
    token: dict, whoop: FakeWhoop, clock: FakeClock, run: Run
) -> None:
    whoop.script("profile", reply(503, retry_after="50"))
    code, result = run("snapshots", time_limit=130)
    assert code == 1
    assert clock.slept == []  # after a 50 s wait, a 30 s attempt would end with 50 s left
    assert len(whoop.data_requests("profile")) == 1
    assert "body_measurement" in result["error"]


# @spec WHOOP-CAPTURE-STOP-001
def test_a_stop_ends_the_unit_without_failures(
    token: dict, whoop: FakeWhoop, unit: Callable[..., Any], data: Path, restore_signals: None
) -> None:
    def stop(request: httpx.Request) -> httpx.Response:
        os.kill(os.getpid(), signal.SIGTERM)
        return whoop.default(request)

    whoop.script("recovery", stop)
    ctx = unit(day=DAY)
    ctx.signals.install()
    code = execute(ctx, ctx.log)
    assert code == 75
    assert ctx.result()["error"] is None
    assert whoop.collections_requested() == ["sleep", "recovery"]
    assert json.loads(list_captures(data, "whoop", "sleep", DAY)[0].payload.read_bytes())
