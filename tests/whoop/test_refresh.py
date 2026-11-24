"""The refresh request, its retry rule, its response, and credential exchanges
(WHOOP-AUTH-REFRESH-003..013, WHOOP-AUTH-EXCH-001..004)."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from whoop_helpers import FakeClock, FakeWhoop, Logs, minutes, read_token, reply, write_token

import wearstream.whoop.files as files
import wearstream.whoop.oauth as oauth
from wearstream import log
from wearstream.runner.context import Signals, Stopped
from wearstream.whoop.tokens import TokenManager, WhoopAuthError

TOKEN = "/oauth/oauth2/token"


@pytest.fixture
def stale(folder: Path, clock: FakeClock) -> Path:
    """A token a scheduled unit refreshes."""
    write_token(folder, clock, refreshed_at=minutes(clock, -45))
    return folder


@pytest.fixture
def restore_signals() -> Iterator[None]:
    saved = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGTERM, saved[0])
    signal.signal(signal.SIGINT, saved[1])


def refresh(folder: Path, signals: Signals | None = None) -> str:
    manager = TokenManager(
        folder, trigger="schedule", signals=signals or Signals(), log=log.get_logger("whoop")
    )
    return manager.access_token()


# @spec WHOOP-AUTH-REFRESH-003
def test_the_refresh_request(stale: Path, whoop: FakeWhoop) -> None:
    refresh(stale)
    [request] = [r for r in whoop.requests if r.url.path == TOKEN]
    assert (request.method, str(request.url)) == ("POST", "https://api.prod.whoop.com" + TOKEN)
    assert whoop.token_forms()[0] == {
        "grant_type": "refresh_token",
        "client_id": "client-abc",
        "client_secret": "CS-SECRET",
        "refresh_token": "RT-0",
        "scope": "offline",
    }


# @spec WHOOP-AUTH-REFRESH-004
def test_each_token_request_gets_a_fresh_client_with_a_5_second_connect_limit() -> None:
    first, second = oauth.token_client(), oauth.token_client()
    try:
        assert first is not second
        assert first.timeout.connect == 5
    finally:
        asyncio.run(first.aclose())
        asyncio.run(second.aclose())


# @spec WHOOP-AUTH-REFRESH-004, WHOOP-AUTH-REFRESH-007
def test_an_attempt_past_its_wall_clock_deadline_is_abandoned_without_retry(
    stale: Path, whoop: FakeWhoop, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    async def slow(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        await asyncio.sleep(2)
        return reply(200, {})

    monkeypatch.setattr(oauth, "ATTEMPT_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr("wearstream.whoop.http.TRANSPORT", httpx.MockTransport(slow))
    with pytest.raises(WhoopAuthError):
        refresh(stale)
    assert len(calls) == 1
    assert read_token(stale)["refresh_failed_at"]


# @spec WHOOP-AUTH-REFRESH-005
def test_no_attempt_is_sent_when_the_folder_cannot_take_a_file(
    stale: Path, whoop: FakeWhoop
) -> None:
    stale.chmod(0o500)
    try:
        with pytest.raises(WhoopAuthError) as raised:
            refresh(stale)
    finally:
        stale.chmod(0o700)
    assert whoop.token_forms() == []
    assert raised.value.needs_person is True


# @spec WHOOP-AUTH-REFRESH-006
def test_connect_failures_are_retried_with_backoff(
    stale: Path, whoop: FakeWhoop, clock: FakeClock
) -> None:
    whoop.script(TOKEN, httpx.ConnectError("refused"), httpx.ConnectTimeout("slow"), whoop.default)
    assert refresh(stale) == "AT-1"
    assert len(whoop.token_forms()) == 3
    assert clock.slept == [1, 2]


# @spec WHOOP-AUTH-REFRESH-006, WHOOP-AUTH-REFRESH-016
def test_three_connect_failures_fail_without_marking_the_token(
    stale: Path, whoop: FakeWhoop
) -> None:
    whoop.script(TOKEN, httpx.ConnectError("refused"))
    with pytest.raises(WhoopAuthError):
        refresh(stale)
    assert len(whoop.token_forms()) == 3
    assert "refresh_failed_at" not in read_token(stale)


# @spec WHOOP-AUTH-REFRESH-007
@pytest.mark.parametrize(
    "answer",
    [reply(500), reply(503), reply(429), httpx.ReadTimeout("x"), httpx.WriteTimeout("x"),
     httpx.RemoteProtocolError("x")],
)  # fmt: skip
def test_anything_that_may_have_reached_whoop_is_never_retried(
    stale: Path, whoop: FakeWhoop, answer: object
) -> None:
    whoop.script(TOKEN, answer)
    with pytest.raises(WhoopAuthError):
        refresh(stale)
    assert len(whoop.token_forms()) == 1


# @spec WHOOP-AUTH-REFRESH-009
def test_a_rotated_token_is_written(stale: Path, whoop: FakeWhoop, clock: FakeClock) -> None:
    refresh(stale)
    doc = read_token(stale)
    assert (doc["access_token"], doc["refresh_token"]) == ("AT-1", "RT-1")
    assert doc["expires_at"] == minutes(clock, 60)
    assert doc["user_id"] == "101"


# @spec WHOOP-AUTH-REFRESH-010
def test_a_200_without_rotation_keeps_the_refresh_token_and_warns(
    stale: Path, whoop: FakeWhoop, logs: Logs
) -> None:
    whoop.script(TOKEN, reply(200, {"access_token": "AT-N", "expires_in": 3600}))
    assert refresh(stale) == "AT-N"
    assert read_token(stale)["refresh_token"] == "RT-0"
    assert logs.events("whoop_token_no_rotation")[0]["level"] == "warning"


# @spec WHOOP-AUTH-REFRESH-011
@pytest.mark.parametrize(
    "body",
    [{"refresh_token": "RT-N", "expires_in": 3600},
     {"refresh_token": "RT-N", "access_token": "AT-N", "expires_in": 0},
     {"refresh_token": "RT-N", "access_token": "AT-N", "expires_in": "3600"}],
)  # fmt: skip
def test_a_malformed_200_carrying_a_rotated_token_saves_it_expired_then_fails(
    stale: Path, whoop: FakeWhoop, clock: FakeClock, body: dict
) -> None:
    whoop.script(TOKEN, reply(200, body))
    with pytest.raises(WhoopAuthError):
        refresh(stale)
    doc = read_token(stale)
    assert doc["refresh_token"] == "RT-N"
    assert doc["expires_at"] == minutes(clock, 0)


# @spec WHOOP-AUTH-REFRESH-012
def test_a_malformed_200_without_a_rotated_token_changes_nothing(
    stale: Path, whoop: FakeWhoop
) -> None:
    before = (stale / "token.json").read_bytes()
    whoop.script(TOKEN, reply(200, {"expires_in": 3600}))
    with pytest.raises(WhoopAuthError):
        refresh(stale)
    assert (stale / "token.json").read_bytes() == before


# @spec WHOOP-AUTH-EXCH-001, WHOOP-AUTH-EXCH-004
def test_a_stop_during_an_exchange_waits_for_the_write(
    stale: Path, whoop: FakeWhoop, restore_signals: None
) -> None:
    def stop_mid_flight(request: httpx.Request) -> httpx.Response:
        os.kill(os.getpid(), signal.SIGTERM)
        return whoop.default(request)

    whoop.script(TOKEN, stop_mid_flight)
    signals = Signals()
    signals.install()
    with pytest.raises(Stopped):
        refresh(stale, signals)
    doc = read_token(stale)
    assert doc["refresh_token"] == "RT-1"
    assert "refresh_failed_at" not in doc


# @spec WHOOP-AUTH-EXCH-002
def test_no_exchange_starts_with_a_stop_pending(stale: Path, whoop: FakeWhoop) -> None:
    signals = Signals()
    signals.received = True
    with pytest.raises(Stopped):
        refresh(stale, signals)
    assert whoop.token_forms() == []


# @spec WHOOP-AUTH-EXCH-003, WHOOP-AUTH-NEED-004
def test_a_token_write_over_5_seconds_counts_as_a_failed_write(
    stale: Path, whoop: FakeWhoop, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    monkeypatch.setattr(files, "WRITE_LIMIT_SECONDS", 0.1)
    real_fsync = os.fsync

    def slow_fsync(fd: int) -> None:
        time.sleep(0.4)
        real_fsync(fd)

    monkeypatch.setattr(files.os, "fsync", slow_fsync)
    with pytest.raises(WhoopAuthError) as raised:
        refresh(stale)
    assert raised.value.needs_person is True
    event = logs.events("whoop_token_folder_unwritable")[0]
    assert "wearstream auth whoop" in event["action"]
