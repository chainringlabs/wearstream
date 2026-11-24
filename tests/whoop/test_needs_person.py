"""When a person is needed, and the credential fingerprint (WHOOP-AUTH-NEED-001..007,
WHOOP-AUTH-FP-001, -002)."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from whoop_helpers import FakeClock, FakeWhoop, Logs, minutes, reply, write_token

from wearstream.settings import paths as resolve
from wearstream.whoop import WhoopSource

TOKEN = "/oauth/oauth2/token"
Run = Callable[..., tuple[int, dict[str, Any]]]


def stale(folder: Path, clock: FakeClock) -> None:
    write_token(folder, clock, refreshed_at=minutes(clock, -45))


def only(logs: Logs, name: str) -> dict[str, Any]:
    [event] = logs.events(name)
    assert event["level"] == "error"
    assert event["action"]
    return event


# @spec WHOOP-AUTH-NEED-001, WHOOP-AUTH-FP-002
def test_no_token_file_needs_a_person(folder: Path, whoop: FakeWhoop, run: Run, logs: Logs) -> None:
    code, result = run()
    assert code == 4
    assert result["needs_credential"] is True and result["fingerprint"]
    assert "wearstream auth whoop" in only(logs, "whoop_credential_missing")["action"]
    assert whoop.requests == []


# @spec WHOOP-AUTH-NEED-002
@pytest.mark.parametrize("damage", ["missing", "empty"])
def test_missing_or_empty_client_files_need_a_person(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run, logs: Logs, damage: str
) -> None:
    stale(folder, clock)
    if damage == "missing":
        (folder / "client_secret").unlink()
    else:
        (folder / "client_id").write_text("  \n")
    assert run()[0] == 4
    assert "client_secret" in only(logs, "whoop_client_credentials_missing")["action"]


# @spec WHOOP-AUTH-NEED-003
@pytest.mark.parametrize("damage", ["json", "key", "permissions"])
def test_an_unreadable_token_needs_a_person(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run, logs: Logs, damage: str
) -> None:
    stale(folder, clock)
    path = folder / "token.json"
    if damage == "json":
        path.write_text("{")
    elif damage == "key":
        write_token(folder, clock, access_token=None)
    else:
        path.chmod(0)
    try:
        assert run()[0] == 4
    finally:
        path.chmod(0o600)
    only(logs, "whoop_token_unreadable")


# @spec WHOOP-AUTH-NEED-004
def test_an_unwritable_folder_needs_a_person_and_names_the_errno(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run, logs: Logs
) -> None:
    stale(folder, clock)
    folder.chmod(0o500)
    try:
        assert run()[0] == 4
    finally:
        folder.chmod(0o700)
    event = only(logs, "whoop_token_folder_unwritable")
    assert event["errno"] == "EACCES"
    assert "writable" in event["action"]
    assert whoop.token_forms() == []


# @spec WHOOP-AUTH-NEED-004
def test_a_failed_write_of_a_rotated_token_says_the_grant_is_lost(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run, logs: Logs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    stale(folder, clock)
    real_replace = os.replace

    def full(src: Any, dst: Any) -> None:
        if str(dst).endswith("token.json"):
            raise OSError(28, "No space left on device")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", full)
    assert run()[0] == 4
    event = only(logs, "whoop_token_folder_unwritable")
    assert event["errno"] == "ENOSPC"
    assert "wearstream auth whoop" in event["action"]


# @spec WHOOP-AUTH-NEED-005
@pytest.mark.parametrize(
    ("status", "error", "event"),
    [
        (400, "invalid_grant", "whoop_token_invalid_grant"),
        (401, "invalid_client", "whoop_client_rejected"),
        (400, "unauthorized_client", "whoop_client_rejected"),
        (400, "invalid_request", "whoop_token_refused"),
        (401, None, "whoop_token_refused"),
    ],
)
def test_a_refused_refresh_is_classified_by_its_oauth_error(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run, logs: Logs,
    status: int, error: str | None, event: str,
) -> None:  # fmt: skip
    stale(folder, clock)
    body = {"error": error, "error_description": "SECRET-DESCRIPTION"} if error else b"not json"
    whoop.script(TOKEN, reply(status, body))
    assert run()[0] == 4
    found = only(logs, event)
    if event == "whoop_token_refused":
        assert (found["status_code"], found.get("oauth_error")) == (status, error)
    if event == "whoop_client_rejected":
        assert "client" in found["action"]
    assert "SECRET-DESCRIPTION" not in logs.text()
    assert whoop.data_requests() == []


# @spec WHOOP-AUTH-NEED-006
def test_a_data_request_answered_403_needs_every_scope(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run, logs: Logs
) -> None:
    write_token(folder, clock)
    whoop.script("sleep", reply(403, {"error": "forbidden"}))
    assert run()[0] == 4
    assert "read:" in only(logs, "whoop_scope_missing")["action"]
    assert len(whoop.data_requests()) == 1


# @spec WHOOP-AUTH-NEED-007
@pytest.mark.parametrize("status", [500, 429])
def test_other_refresh_failures_are_failed_requests(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run, logs: Logs, status: int
) -> None:
    stale(folder, clock)
    whoop.script(TOKEN, reply(status))
    code, result = run()
    assert (code, result["needs_credential"]) == (1, False)
    assert logs.events("whoop_token_refresh_failed")[0]["level"] == "warning"


def _fingerprint(env: dict[str, str]) -> str:
    from wearstream.settings import load

    return WhoopSource().fingerprint(resolve(load(env, "status").values))


# @spec WHOOP-AUTH-FP-001
def test_the_fingerprint_changes_with_the_files_but_never_reads_them(
    folder: Path, clock: FakeClock, env: dict[str, str]
) -> None:
    write_token(folder, clock)
    first = _fingerprint(env)
    assert _fingerprint(env) == first
    token = folder / "token.json"
    stamp = token.stat()
    token.write_text(token.read_text().replace("RT-0", "RT-9"))  # same size
    os.utime(token, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    token.chmod(0)
    try:
        locked = _fingerprint(env)
    finally:
        token.chmod(stat.S_IMODE(stamp.st_mode))
    assert locked != first  # the mode changed; the contents were never read
    os.utime(token, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert _fingerprint(env) == first
    (folder / "client_secret").write_text("CS-NEW-SECRET\n")
    assert _fingerprint(env) != first
    folder.chmod(0o500)
    try:
        assert _fingerprint(env) != first
    finally:
        folder.chmod(0o700)
