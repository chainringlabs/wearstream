"""Credential-safe logging (WHOOP-AUTH-LOG-001..004)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from whoop_helpers import FakeClock, FakeWhoop, Logs, minutes, reply, write_token

from wearstream import log
from wearstream.runner.context import Signals
from wearstream.whoop.tokens import TokenManager, WhoopAuthError

Run = Callable[..., tuple[int, dict[str, Any]]]
TOKEN = "/oauth/oauth2/token"
SECRETS = ("AT-0", "RT-0", "AT-1", "RT-1", "CS-SECRET", "SECRET-BODY", "SECRET-DESCRIPTION")


def stale(folder: Path, clock: FakeClock) -> None:
    write_token(folder, clock, refreshed_at=minutes(clock, -45))


def assert_clean(logs: Logs, capfd: pytest.CaptureFixture[str]) -> None:
    text = logs.text() + capfd.readouterr().out
    for secret in SECRETS:
        assert secret not in text


# @spec WHOOP-AUTH-LOG-001
@pytest.mark.parametrize("where", [TOKEN, "sleep"])
def test_unexpected_failures_log_only_their_type(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run, logs: Logs, where: str
) -> None:
    stale(folder, clock)
    whoop.script(where, RuntimeError("Authorization: Bearer AT-0 RT-0"))
    run(day="2026-11-20")
    failures = [e for e in logs.events() if e.get("error_type") == "RuntimeError"]
    assert failures
    for event in failures:
        assert "traceback" not in event and "error" not in event
    assert "Bearer" not in logs.text()


# @spec WHOOP-AUTH-LOG-002
def test_a_re_raised_failure_carries_only_the_type_name(
    folder: Path, clock: FakeClock, whoop: FakeWhoop
) -> None:
    stale(folder, clock)
    whoop.script(TOKEN, httpx.ReadTimeout("response held RT-0"))
    manager = TokenManager(folder, trigger="schedule", signals=Signals(), log=log.get_logger("w"))
    with pytest.raises(WhoopAuthError) as raised:
        manager.access_token()
    error = raised.value
    assert "RT-0" not in str(error) and "ReadTimeout" in str(error)
    assert error.__cause__ is None and error.__suppress_context__ is True


# @spec WHOOP-AUTH-LOG-003, WHOOP-AUTH-LOG-004
def test_no_credential_or_body_reaches_the_log_on_any_path(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run, logs: Logs,
    capfd: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    stale(folder, clock)
    whoop.script("recovery", reply(500, {"message": "SECRET-BODY"}))
    whoop.script("workout", reply(404, {"message": "SECRET-BODY"}))
    run(day="2026-11-20", time_limit=3600)
    run("snapshots")
    assert_clean(logs, capfd)
    whoop.script(
        TOKEN, reply(400, {"error": "invalid_grant", "error_description": "SECRET-DESCRIPTION"})
    )
    write_token(folder, clock, refreshed_at=minutes(clock, -45))
    run("snapshots")
    assert_clean(logs, capfd)
    assert logs.events("whoop_token_invalid_grant")
