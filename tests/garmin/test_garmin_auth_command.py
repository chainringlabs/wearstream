"""`wearstream auth garmin` (GARMIN-AUTH-BOOT-001..009)."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from garmin_helpers import EMAIL, PASSWORD, SESSION, FakeLibrary
from garminconnect import GarminConnectConnectionError

import wearstream.garmin.bootstrap as bootstrap
from wearstream import cli, jobs

Answer = Callable[[str], str]


@pytest.fixture(autouse=True)
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)


def prompts(monkeypatch: pytest.MonkeyPatch, answer: Answer | None = None) -> list[str]:
    """Answer `input` and `getpass`; returns the prompts shown."""
    shown: list[str] = []

    def reply(prompt: str = "") -> str:
        shown.append(prompt)
        if answer:
            return answer(prompt)
        lowered = prompt.lower()
        return EMAIL if "email" in lowered else PASSWORD if "password" in lowered else "123456"

    monkeypatch.setattr("builtins.input", reply)
    monkeypatch.setattr("getpass.getpass", reply)
    return shown


def authorize(capfd: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli.main(["auth", "garmin", *args])
    return code, capfd.readouterr().out


def written(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name not in ("email", "password"))


# @spec GARMIN-AUTH-BOOT-001
def test_without_the_state_lock_folder_it_refuses(
    state: Path, garmin: FakeLibrary, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    prompts(monkeypatch)
    (state / "locks").rmdir()
    code, out = authorize(capfd)
    assert code == 2 and "/state" in out
    assert garmin.clients == []


# @spec GARMIN-AUTH-BOOT-002, GARMIN-AUTH-BOOT-003, GARMIN-AUTH-BOOT-007
def test_prompted_credentials_sign_in_in_memory_then_the_session_is_written(
    folder: Path, state: Path, garmin: FakeLibrary, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    shown = prompts(monkeypatch)
    code, out = authorize(capfd)
    assert code == 0
    assert len(shown) == 2  # the email, then the password without echo
    [client] = garmin.clients
    assert (client.email, client.password) == (EMAIL, PASSWORD)
    assert garmin.logins == [("password", None)]
    token = folder / "garmin_tokens.json"
    assert json.loads(token.read_text()) == SESSION
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert json.loads((folder / "account.json").read_text())["profile_id"] == 12345
    assert "Runner" in out and "12345" in out
    for secret in (PASSWORD, *SESSION.values()):
        assert secret not in out
    for root in (folder, state):
        for path in root.rglob("*"):
            if path.is_file():
                assert PASSWORD not in path.read_text(errors="ignore")


# @spec GARMIN-AUTH-BOOT-002
def test_credential_files_are_used_without_prompting(
    folder: Path, credentials: None, garmin: FakeLibrary, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    shown = prompts(monkeypatch)
    assert authorize(capfd)[0] == 0
    assert shown == []


# @spec GARMIN-AUTH-BOOT-004
def test_an_mfa_code_is_asked_for(
    folder: Path, credentials: None, garmin: FakeLibrary, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    garmin.password_login = "mfa"
    shown = prompts(monkeypatch)
    assert authorize(capfd)[0] == 0
    assert len(shown) == 1 and "code" in shown[0].lower()


# @spec GARMIN-AUTH-BOOT-004
@pytest.mark.parametrize("problem", ["timeout", "wrong"])
def test_an_unanswered_or_wrong_mfa_code_exits_2(
    folder: Path, credentials: None, garmin: FakeLibrary, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch, problem: str,
) -> None:  # fmt: skip
    garmin.password_login = "mfa"
    monkeypatch.setattr(bootstrap, "MFA_TIMEOUT_SECONDS", 0.2)

    def slow(_prompt: str) -> str:
        if problem == "timeout":
            time.sleep(1)
        return "000000"

    prompts(monkeypatch, slow)
    assert authorize(capfd)[0] == 2
    assert written(folder) == []


# @spec GARMIN-AUTH-BOOT-005
def test_a_session_that_cannot_be_resumed_is_refused(
    folder: Path, credentials: None, garmin: FakeLibrary, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    garmin.session = {"di_token": "", "di_refresh_token": "", "di_client_id": None}
    prompts(monkeypatch)
    assert authorize(capfd)[0] == 1
    assert written(folder) == []


# @spec GARMIN-AUTH-BOOT-006
def test_a_different_account_needs_new_account(
    folder: Path, credentials: None, garmin: FakeLibrary, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    (folder / "account.json").write_text(json.dumps({"profile_id": 999}))
    prompts(monkeypatch)
    code, out = authorize(capfd)
    assert code == 2 and "999" in out and "12345" in out and "--new-account" in out
    assert written(folder) == ["account.json"]
    assert authorize(capfd, "--new-account")[0] == 0
    assert json.loads((folder / "account.json").read_text())["profile_id"] == 12345


def _free(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


# @spec GARMIN-AUTH-BOOT-008
def test_no_lock_is_held_while_waiting_for_the_operator(
    folder: Path, state: Path, garmin: FakeLibrary, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    garmin.password_login = "mfa"
    free: list[bool] = []

    def answer(prompt: str) -> str:
        free.append(_free(state / "locks" / "garmin.lock"))
        lowered = prompt.lower()
        return EMAIL if "email" in lowered else PASSWORD if "password" in lowered else "123456"

    prompts(monkeypatch, answer)
    assert authorize(capfd)[0] == 0
    assert free == [True, True, True]


# @spec GARMIN-AUTH-BOOT-007
def test_a_running_unit_makes_it_wait_then_give_up(
    folder: Path, state: Path, credentials: None, garmin: FakeLibrary,
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr(jobs, "SECONDS_PER_MINUTE", 0.01)
    monkeypatch.setattr(bootstrap, "EXTRA_WAIT_SECONDS", 0.1)
    prompts(monkeypatch)
    fd = os.open(state / "locks" / "garmin.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        code, out = authorize(capfd)
    finally:
        os.close(fd)
    assert code == 5 and "waiting" in out.lower()
    assert written(folder) == []


# @spec GARMIN-AUTH-BOOT-009
def test_any_other_sign_in_failure_exits_1(
    folder: Path, credentials: None, garmin: FakeLibrary, capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    garmin.password_login = GarminConnectConnectionError("Login failed: upstream")
    prompts(monkeypatch)
    code, out = authorize(capfd)
    assert code == 1 and "GarminConnectConnectionError" in out
    assert "upstream" not in out
    assert written(folder) == []
