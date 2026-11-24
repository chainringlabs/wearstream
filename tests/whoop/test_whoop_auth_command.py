"""`wearstream auth whoop` (WHOOP-AUTH-BOOT-001..012, OPERATOR-CMD-001, -002)."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from whoop_helpers import FakeClock, FakeWhoop, read_token, reply, write_token

import wearstream.whoop.bootstrap as bootstrap
from wearstream import cli, jobs

TOKEN = "/oauth/oauth2/token"
REDIRECT = "http://localhost:8000/callback"
Paste = Callable[[dict[str, str]], str]


@pytest.fixture(autouse=True)
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)


def printed_url(text: str) -> dict[str, str]:
    url = next(word for word in text.split() if word.startswith("https://"))
    parts = urlsplit(url)
    return {"base": f"{parts.scheme}://{parts.netloc}{parts.path}"} | {
        k: v[-1] for k, v in parse_qs(parts.query).items()
    }


def authorize(
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    paste: Paste = lambda q: f"{REDIRECT}?code=CODE-SECRET&state={q['state']}",
    args: tuple[str, ...] = (),
) -> tuple[int, str, dict[str, str]]:
    seen: dict[str, str] = {}

    def fake_input(_prompt: str = "") -> str:
        seen.update(printed_url(capfd.readouterr().out))
        return paste(seen)

    monkeypatch.setattr("builtins.input", fake_input)
    code = cli.main(["auth", "whoop", *args])
    return code, capfd.readouterr().out, seen


def token_requests(whoop: FakeWhoop) -> list[dict[str, str]]:
    return whoop.token_forms()


# @spec WHOOP-AUTH-BOOT-001
def test_without_the_state_lock_folder_it_refuses(
    folder: Path, state: Path, whoop: FakeWhoop, capfd: pytest.CaptureFixture[str]
) -> None:
    (state / "locks").rmdir()
    assert cli.main(["auth", "whoop"]) == 2
    assert "/state" in capfd.readouterr().out
    assert whoop.requests == []


# @spec WHOOP-AUTH-BOOT-002, WHOOP-AUTH-BOOT-003, WHOOP-AUTH-BOOT-011
def test_authorizing_writes_the_token_and_prints_no_secret(
    folder: Path, whoop: FakeWhoop, clock: FakeClock,
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    code, out, url = authorize(
        capfd,
        monkeypatch,
        lambda q: f"  '{REDIRECT}?state={q['state']}&code=CODE%2BSECRET#frag'\n",
    )
    assert code == 0
    assert url["base"] == "https://api.prod.whoop.com/oauth/oauth2/auth"
    assert (url["client_id"], url["redirect_uri"], url["response_type"]) == (
        "client-abc",
        REDIRECT,
        "code",
    )
    assert url["scope"].split() == [
        "read:sleep", "read:workout", "read:recovery", "read:cycles", "read:profile",
        "read:body_measurement", "offline",
    ]  # fmt: skip
    assert url["code_challenge_method"] == "S256" and len(url["state"]) >= 16
    [form] = token_requests(whoop)
    assert (form["grant_type"], form["code"], form["redirect_uri"]) == (
        "authorization_code",
        "CODE+SECRET",
        REDIRECT,
    )
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
    assert challenge.rstrip(b"=").decode() == url["code_challenge"]
    doc = read_token(folder)
    assert (doc["refresh_token"], doc["user_id"]) == ("RT-1", "101")
    assert doc["refreshed_at"] and doc["expires_at"]
    assert not (folder / "token.json.new").exists()
    assert "101" in out and "read:sleep" in out
    for secret in ("AT-1", "RT-1", "CODE+SECRET", "CS-SECRET", form["code_verifier"]):
        assert secret not in out


# @spec WHOOP-AUTH-BOOT-004
@pytest.mark.parametrize(
    ("paste", "said"),
    [
        (lambda q: f"{REDIRECT}?error=access_denied&state={q['state']}", "access_denied"),
        (lambda q: f"{REDIRECT}?state={q['state']}", "code"),
        (lambda q: f"{REDIRECT}?code=C", "state"),
        (lambda q: f"{REDIRECT}?code=C&state=other", "state"),
        (lambda q: "CODE-ONLY", "address"),
    ],
)
def test_unusable_pastes_exit_2_without_an_exchange(
    folder: Path, whoop: FakeWhoop, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch, paste: Paste, said: str,
) -> None:  # fmt: skip
    code, out, _url = authorize(capfd, monkeypatch, paste)
    assert code == 2
    assert said in out
    assert token_requests(whoop) == []


# @spec WHOOP-AUTH-BOOT-005
def test_a_running_whoop_unit_makes_it_wait_then_give_up(
    folder: Path, state: Path, whoop: FakeWhoop, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr(jobs, "SECONDS_PER_MINUTE", 0.01)
    monkeypatch.setattr(bootstrap, "EXTRA_WAIT_SECONDS", 0.1)
    fd = os.open(state / "locks" / "whoop.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        code, out, _url = authorize(capfd, monkeypatch)
    finally:
        os.close(fd)
    assert code == 5
    assert "waiting" in out.lower()
    assert token_requests(whoop) == []


def _free(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


# @spec WHOOP-AUTH-BOOT-006
def test_no_lock_is_held_while_the_operator_is_in_the_browser(
    folder: Path, state: Path, whoop: FakeWhoop, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    free: list[bool] = []

    def paste(q: dict[str, str]) -> str:
        free.extend([_free(state / "locks" / "whoop.lock"), _free(folder / "token.json.lock")])
        return f"{REDIRECT}?code=C&state={q['state']}"

    assert authorize(capfd, monkeypatch, paste)[0] == 0
    assert free == [True, True]


# @spec WHOOP-AUTH-BOOT-007
def test_the_exchange_stages_the_token_under_the_token_lock(
    folder: Path, state: Path, whoop: FakeWhoop, clock: FakeClock,
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    old = write_token(folder, clock)
    during: dict[str, Any] = {}

    def profile(request: Any) -> Any:
        during["staged"] = json.loads((folder / "token.json.new").read_text())
        during["current"] = read_token(folder)
        during["source_lock_free"] = _free(state / "locks" / "whoop.lock")
        return reply(200, {"user_id": 101})

    whoop.script("profile", profile)
    assert authorize(capfd, monkeypatch)[0] == 0
    assert during["staged"]["refresh_token"] == "RT-1"
    assert during["current"] == old
    assert during["source_lock_free"] is False


# @spec WHOOP-AUTH-BOOT-008
@pytest.mark.parametrize("missing", ["refresh_token", "scope"])
def test_a_grant_without_a_refresh_token_or_a_scope_is_refused(
    folder: Path, whoop: FakeWhoop, clock: FakeClock, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:  # fmt: skip
    old = write_token(folder, clock)
    scope = "read:sleep read:workout read:recovery read:cycles read:profile offline"
    body = {"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 3600, "scope": scope}
    if missing == "refresh_token":
        body = {**body, "scope": body["scope"] + " read:body_measurement"}
        del body["refresh_token"]
    whoop.script(TOKEN, reply(200, body))
    code, out, _url = authorize(capfd, monkeypatch)
    assert code == 1
    assert ("refresh token" if missing == "refresh_token" else "read:body_measurement") in out
    assert read_token(folder) == old
    assert not (folder / "token.json.new").exists()


# @spec WHOOP-AUTH-BOOT-009
@pytest.mark.parametrize("failure", ["exchange", "profile"])
def test_a_failed_exchange_or_profile_request_writes_nothing(
    folder: Path, whoop: FakeWhoop, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:  # fmt: skip
    whoop.script(TOKEN if failure == "exchange" else "profile", reply(500))
    assert authorize(capfd, monkeypatch)[0] == 1
    assert not (folder / "token.json").exists()
    assert not (folder / "token.json.new").exists()


# @spec WHOOP-AUTH-BOOT-010
def test_a_different_account_needs_new_account(
    folder: Path, whoop: FakeWhoop, clock: FakeClock, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    old = write_token(folder, clock, user_id="555")
    code, out, _url = authorize(capfd, monkeypatch)
    assert code == 2
    assert "555" in out and "101" in out and "--new-account" in out
    assert read_token(folder) == old
    assert not (folder / "token.json.new").exists()
    code, _out, _url = authorize(capfd, monkeypatch, args=("--new-account",))
    assert code == 0
    assert read_token(folder)["user_id"] == "101"


# @spec WHOOP-AUTH-BOOT-012
def test_it_never_listens(
    folder: Path, whoop: FakeWhoop, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    def refuse(*_args: Any) -> None:
        raise AssertionError("auth must not listen")

    monkeypatch.setattr(socket.socket, "listen", refuse)
    monkeypatch.setattr(socket.socket, "bind", refuse)
    assert authorize(capfd, monkeypatch)[0] == 0


# @spec OPERATOR-CMD-002
def test_without_a_terminal_it_says_how_to_run_it(
    folder: Path, whoop: FakeWhoop, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["auth", "whoop"]) == 2
    assert "docker exec -it" in capfd.readouterr().out
    assert whoop.requests == []


# @spec OPERATOR-CMD-001
def test_every_command_is_offered_with_help(capfd: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as done:
        cli.main(["--help"])
    assert done.value.code == 0
    listing = capfd.readouterr().out
    for command in ("daemon", "run", "auth", "accept-schema", "status", "config", "healthcheck",
                    "version"):  # fmt: skip
        assert command in listing
        with pytest.raises(SystemExit) as done:
            cli.main([command, "--help"])
        assert done.value.code == 0
