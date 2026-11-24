"""Rate limits, session refresh as a credential exchange, and the library's environment
(GARMIN-AUTH-CLASS-004, -SIGNIN-005, -EXCH-001, -002, -STORE-002..004)."""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from garmin_helpers import FakeLibrary, Logs
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from wearstream.runner.run import execute

Run = Callable[..., tuple[int, dict[str, Any]]]


@pytest.fixture
def restore_signals() -> Iterator[None]:
    saved = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, saved)


def only(logs: Logs, name: str, level: str) -> dict[str, Any]:
    [event] = logs.events(name)
    assert event["level"] == level
    return event


def _with_429_cause() -> Exception:
    class Response:
        status_code = 429

    inner = GarminConnectConnectionError("upstream")
    inner.response = Response()
    try:
        try:
            raise inner
        except GarminConnectConnectionError as exc:
            raise GarminConnectAuthenticationError("profile failed") from exc
    except GarminConnectAuthenticationError as outer:
        return outer


# @spec GARMIN-AUTH-CLASS-004
@pytest.mark.parametrize("cause", ["too_many", "chain", "refresh"])
def test_a_rate_limit_at_any_step_ends_the_unit(
    session: Path, credentials: None, garmin: FakeLibrary, run: Run, logs: Logs, cause: str
) -> None:
    if cause == "too_many":
        garmin.resume = [GarminConnectTooManyRequestsError("429")]
    elif cause == "chain":
        garmin.resume = [_with_429_cause()]
    else:
        garmin.refresh_on_resume, garmin.refresh_status = True, 429
        garmin.resume = [GarminConnectAuthenticationError("401 Unauthorized")]
    code, result = run()
    assert (code, result["rate_limited"]) == (6, True)
    only(logs, "garmin_rate_limited", "warning")
    assert [kind for kind, _store in garmin.logins] == ["resume"]
    assert garmin.calls == []


# @spec GARMIN-AUTH-CLASS-004
def test_a_rate_limit_on_a_data_call_ends_the_unit(
    session: Path, garmin: FakeLibrary, run: Run
) -> None:
    garmin.answers["get_steps_data"] = GarminConnectTooManyRequestsError("429")
    code, result = run()
    assert code == 6
    assert garmin.methods()[-1] == "get_steps_data"


# @spec GARMIN-AUTH-SIGNIN-005
def test_a_rejection_mid_unit_stops_calling(
    session: Path, garmin: FakeLibrary, run: Run, logs: Logs
) -> None:
    garmin.answers["get_steps_data"] = GarminConnectAuthenticationError("session refused")
    code, result = run()
    assert (code, result["needs_credential"]) == (1, False)
    assert garmin.methods()[-1] == "get_steps_data"
    only(logs, "garmin_signin_failed", "warning")


# @spec GARMIN-AUTH-EXCH-001
def test_a_stop_during_a_session_refresh_waits_for_the_save(
    session: Path, garmin: FakeLibrary, unit: Callable[..., Any], folder: Path,
    restore_signals: None,
) -> None:  # fmt: skip
    garmin.refresh_on_resume = True
    garmin.during_refresh = lambda: os.kill(os.getpid(), signal.SIGTERM)
    ctx = unit()
    ctx.signals.install()
    assert execute(ctx, ctx.log) == 75
    saved = json.loads((folder / "garmin_tokens.json").read_text())
    assert saved["di_refresh_token"] == "DI-REFRESH-ROTATED"
    assert garmin.calls == []


# @spec GARMIN-AUTH-EXCH-002
def test_no_refresh_starts_with_a_stop_pending(
    session: Path, garmin: FakeLibrary, unit: Callable[..., Any]
) -> None:
    garmin.refresh_on_resume = True
    ctx = unit()
    ctx.signals.received = True
    assert execute(ctx, ctx.log) == 75
    assert garmin.refreshes == 0


# @spec GARMIN-AUTH-STORE-002
def test_garmintokens_is_removed_from_the_environment(
    session: Path, garmin: FakeLibrary, run: Run, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GARMINTOKENS", '{"di_token": "INLINE-SECRET"}')
    assert run()[0] == 0
    assert "GARMINTOKENS" not in os.environ
    only(logs, "garmin_tokens_env_ignored", "warning")
    assert "INLINE-SECRET" not in logs.text()


# @spec GARMIN-AUTH-STORE-003
def test_the_token_file_is_located_by_the_library(
    session: Path, garmin: FakeLibrary, run: Run, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wearstream.garmin.library as library

    asked: list[str] = []
    real = library.token_file_path

    def spy(path: str) -> Path:
        asked.append(path)
        return real(path)

    monkeypatch.setattr(library, "token_file_path", spy)
    run()
    assert asked and set(asked) == {str(folder)}


# @spec GARMIN-AUTH-STORE-004
def test_the_china_site(credentials: None, garmin: FakeLibrary, run: Run) -> None:
    run(WEARSTREAM_GARMIN_CHINA="true")
    assert [c.is_cn for c in garmin.clients] == [True, True]
