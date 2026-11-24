"""A unit's sign-in, how a failure is classified, and session refresh as a credential exchange
(GARMIN-AUTH-SIGNIN-001..004, -006..008, -CLASS-001..003, -005)."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from garmin_helpers import NOW, FakeClock, FakeLibrary, Logs
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)

from wearstream.runner.run import execute
from wearstream.state import iso

Run = Callable[..., tuple[int, dict[str, Any]]]
REFUSED = GarminConnectAuthenticationError("401 Unauthorized (Invalid Username or Password)")


def only(logs: Logs, name: str, level: str) -> dict[str, Any]:
    [event] = logs.events(name)
    assert event["level"] == level
    return event


def signin_doc(state: Path) -> dict[str, Any]:
    return json.loads((state / "garmin" / "signin.json").read_text())


# @spec GARMIN-AUTH-SIGNIN-001
def test_a_unit_resumes_without_credentials_and_uses_that_client(
    session: Path, credentials: None, garmin: FakeLibrary, run: Run, folder: Path
) -> None:
    assert run()[0] == 0
    [client] = garmin.clients
    assert (client.email, client.password) == (None, None)
    assert garmin.logins == [("resume", str(folder))]


# @spec GARMIN-AUTH-SIGNIN-006, GARMIN-AUTH-SIGNIN-003
@pytest.mark.parametrize("store", ["missing", "unloadable", "refused"])
def test_no_usable_session_leads_to_one_password_sign_in(
    credentials: None, garmin: FakeLibrary, run: Run, folder: Path, state: Path, store: str
) -> None:
    if store == "unloadable":
        (folder / "garmin_tokens.json").write_text("{")
    if store == "refused":
        (folder / "garmin_tokens.json").write_text(json.dumps({"di_token": "old"}))
        garmin.resume = [GarminConnectAuthenticationError("401 Unauthorized"), "ok"]
    assert run()[0] == 0
    assert [kind for kind, _store in garmin.logins] == ["resume", "password"]
    assert (garmin.clients[1].email, garmin.clients[1].password) != (None, None)
    assert signin_doc(state)["last_password_signin"] == iso(NOW)


# @spec GARMIN-AUTH-SIGNIN-006, GARMIN-AUTH-SIGNIN-004, GARMIN-AUTH-CLASS-005
def test_other_resume_failures_are_retried_never_signed_in_with_a_password(
    session: Path, credentials: None, garmin: FakeLibrary, clock: FakeClock, run: Run, logs: Logs
) -> None:
    garmin.resume = [GarminConnectConnectionError("500 from profile")]
    code, _result = run()
    assert code == 1
    assert [kind for kind, _store in garmin.logins] == ["resume"] * 3
    assert clock.slept[:2] == [2, 4]
    assert all(c.password is None for c in garmin.clients)
    event = logs.events("garmin_signin_failed")[-1]
    assert (event["level"], event["error_type"]) == ("warning", "GarminConnectConnectionError")


# @spec GARMIN-AUTH-SIGNIN-004
def test_a_resume_that_recovers_on_retry_continues(
    session: Path, garmin: FakeLibrary, run: Run
) -> None:
    garmin.resume = [GarminConnectConnectionError("blip"), "ok"]
    assert run()[0] == 0
    assert len(garmin.clients) == 2


# @spec GARMIN-AUTH-SIGNIN-007
def test_a_stop_pending_when_sign_in_fails_ends_the_unit_unclassified(
    session: Path, garmin: FakeLibrary, unit: Callable[..., Any], logs: Logs
) -> None:
    ctx = unit()

    def stop_then_fail() -> None:
        ctx.signals.received = True
        raise GarminConnectAuthenticationError("Username and password are required")

    garmin.resume = [stop_then_fail]
    assert execute(ctx, ctx.log) == 75
    assert not [
        n for n in logs.names() if n.startswith("garmin_") and n != "garmin_tokens_env_ignored"
    ]


# @spec GARMIN-AUTH-SIGNIN-002, GARMIN-AUTH-CLASS-001
def test_mfa_in_a_unit_needs_a_person_and_never_prompts(
    credentials: None, garmin: FakeLibrary, run: Run, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_prompt(*_args: Any) -> str:
        raise AssertionError("a unit must never prompt")

    monkeypatch.setattr("builtins.input", no_prompt)
    monkeypatch.setattr("getpass.getpass", no_prompt)
    garmin.password_login = "mfa"
    code, result = run()
    assert code == 4 and result["fingerprint"]
    assert "wearstream auth garmin" in only(logs, "garmin_mfa_required", "error")["action"]
    assert [kind for kind, _store in garmin.logins] == ["resume", "password"]


# @spec GARMIN-AUTH-SIGNIN-003
@pytest.mark.parametrize(("minutes_ago", "wait"), [(10, 3000), (59.99, 1)])
def test_at_most_one_password_sign_in_an_hour(
    credentials: None, garmin: FakeLibrary, run: Run, state: Path, logs: Logs,
    minutes_ago: float, wait: int,
) -> None:  # fmt: skip
    import datetime as dt

    (state / "garmin").mkdir()
    last = iso(NOW - dt.timedelta(minutes=minutes_ago))
    (state / "garmin" / "signin.json").write_text(json.dumps({"last_password_signin": last}))
    code, result = run()
    assert code == 6
    assert result["retry_after_seconds"] == wait
    assert [kind for kind, _store in garmin.logins] == ["resume"]
    only(logs, "garmin_signin_deferred", "warning")


# @spec GARMIN-AUTH-SIGNIN-008
@pytest.mark.parametrize("problem", ["lock", "unwritable"])
def test_no_password_sign_in_without_recording_it(
    credentials: None, garmin: FakeLibrary, run: Run, state: Path,
    monkeypatch: pytest.MonkeyPatch, problem: str,
) -> None:  # fmt: skip
    monkeypatch.setattr("wearstream.garmin.signin.SIGNIN_LOCK_WAIT", 0.1)
    fd = None
    if problem == "lock":
        fd = os.open(state / "locks" / "state.lock", os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
    else:
        (state / "garmin").mkdir(mode=0o500)
    try:
        code, _result = run()
    finally:
        if fd is not None:
            os.close(fd)
        (state / "garmin").chmod(0o700) if (state / "garmin").exists() else None
    assert code == 1
    assert [kind for kind, _store in garmin.logins] == ["resume"]


# @spec GARMIN-AUTH-CLASS-002
def test_rejected_credentials_need_a_person_and_are_never_retried(
    credentials: None, garmin: FakeLibrary, run: Run, logs: Logs
) -> None:
    garmin.password_login = REFUSED
    assert run()[0] == 4
    assert "password" in only(logs, "garmin_signin_refused", "error")["action"]
    assert [kind for kind, _store in garmin.logins] == ["resume", "password"]


# @spec GARMIN-AUTH-CLASS-005
def test_an_unplaceable_authentication_error_is_transient(
    credentials: None, garmin: FakeLibrary, run: Run, logs: Logs
) -> None:
    garmin.password_login = GarminConnectAuthenticationError("JWT_WEB cookie not set")
    assert run()[0] == 1
    only(logs, "garmin_signin_failed", "warning")
    assert [kind for kind, _store in garmin.logins] == ["resume", "password"]


# @spec GARMIN-AUTH-CLASS-003
@pytest.mark.parametrize("missing", ["email", "password"])
def test_no_session_and_no_credentials_needs_a_person(
    credentials: None, garmin: FakeLibrary, run: Run, logs: Logs, folder: Path, missing: str
) -> None:
    (folder / missing).unlink()
    assert run()[0] == 4
    only(logs, "garmin_credentials_missing", "error")
    assert [kind for kind, _store in garmin.logins] == ["resume"]
