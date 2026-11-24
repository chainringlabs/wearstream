"""Token storage and when a unit refreshes (WHOOP-AUTH-STORE-001..003, WHOOP-AUTH-REFRESH-001,
-002, -014, -015, -016)."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest
from whoop_helpers import FakeClock, FakeWhoop, Logs, minutes, read_token, reply, write_token

from wearstream import log
from wearstream.runner.context import Signals
from wearstream.whoop.tokens import TokenManager, TokenStore, TokenUnreadable, WhoopAuthError


def manager(folder: Path, trigger: str = "schedule") -> TokenManager:
    return TokenManager(folder, trigger=trigger, signals=Signals(), log=log.get_logger("whoop"))


def refreshes(whoop: FakeWhoop) -> int:
    return len(whoop.token_forms())


# @spec WHOOP-AUTH-STORE-001
def test_client_files_are_read_ignoring_whitespace(folder: Path) -> None:
    assert TokenStore(folder).client() == ("client-abc", "CS-SECRET")


# @spec WHOOP-AUTH-STORE-002
def test_writes_are_whole_private_and_keep_other_keys(folder: Path, clock: FakeClock) -> None:
    write_token(folder, clock, custom="kept")
    TokenStore(folder).write({"access_token": "AT-9"})
    doc = read_token(folder)
    assert (doc["access_token"], doc["refresh_token"], doc["custom"]) == ("AT-9", "RT-0", "kept")
    assert stat.S_IMODE((folder / "token.json").stat().st_mode) == 0o600
    assert sorted(p.name for p in folder.iterdir()) == ["client_id", "client_secret", "token.json"]


# @spec WHOOP-AUTH-STORE-003
@pytest.mark.parametrize("damage", ["not json", "a list", "no refresh_token", "no expires_at"])
def test_a_token_file_missing_a_required_key_or_not_an_object_is_unreadable(
    folder: Path, clock: FakeClock, damage: str
) -> None:
    path = folder / "token.json"
    if damage == "not json":
        path.write_text("{")
    elif damage == "a list":
        path.write_text("[]")
    else:
        write_token(folder, clock, **{damage.removeprefix("no "): None})
    with pytest.raises(TokenUnreadable):
        TokenStore(folder).read()


# @spec WHOOP-AUTH-REFRESH-001
@pytest.mark.parametrize(
    ("fields", "refreshed"),
    [
        ({}, False),
        ({"refreshed_at": None}, True),
        ({"refreshed_at": "-31"}, True),
        ({"refreshed_at": "-29"}, False),
        ({"refreshed_at": "-1", "expires_at": "4"}, True),
    ],
)
def test_a_scheduled_unit_refreshes_a_token_refreshed_over_30_minutes_ago_or_near_expiry(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, fields: dict, refreshed: bool
) -> None:
    write_token(folder, clock, **{k: v and minutes(clock, float(v)) for k, v in fields.items()})
    got = manager(folder).access_token()
    assert refreshes(whoop) == int(refreshed)
    assert got == ("AT-1" if refreshed else "AT-0")


# @spec WHOOP-AUTH-REFRESH-001
def test_the_units_of_a_tick_share_one_refresh(
    folder: Path, clock: FakeClock, whoop: FakeWhoop
) -> None:
    write_token(folder, clock, refreshed_at=minutes(clock, -45))
    assert manager(folder).access_token() == "AT-1"
    assert manager(folder).access_token() == "AT-1"
    assert refreshes(whoop) == 1


# @spec WHOOP-AUTH-REFRESH-014
@pytest.mark.parametrize("trigger", ["catchup", "retry", "backfill", "manual"])
def test_other_triggers_use_an_unexpired_token_however_old(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, trigger: str
) -> None:
    write_token(folder, clock, refreshed_at=minutes(clock, -55), expires_at=minutes(clock, 1))
    assert manager(folder, trigger).access_token() == "AT-0"
    assert refreshes(whoop) == 0


# @spec WHOOP-AUTH-REFRESH-014
@pytest.mark.parametrize(("minute", "allowed"), [(5, False), (6, True), (54, True), (55, False)])
def test_other_triggers_refresh_an_expired_token_only_mid_hour(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, minute: int, allowed: bool
) -> None:
    clock.set_wall(dt.datetime(2026, 11, 24, 10, minute, 30, tzinfo=dt.UTC))
    write_token(folder, clock, expires_at=minutes(clock, -1))
    if allowed:
        assert manager(folder, "backfill").access_token() == "AT-1"
    else:
        with pytest.raises(WhoopAuthError):
            manager(folder, "backfill").access_token()
        assert refreshes(whoop) == 0


# @spec WHOOP-AUTH-REFRESH-015
def test_a_deferred_refresh_warns_and_is_a_failed_request(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, logs: Logs
) -> None:
    clock.set_wall(dt.datetime(2026, 11, 24, 10, 58, tzinfo=dt.UTC))
    write_token(folder, clock, expires_at=minutes(clock, -1))
    with pytest.raises(WhoopAuthError) as raised:
        manager(folder, "catchup").access_token()
    assert raised.value.needs_person is False
    assert logs.events("whoop_refresh_deferred")[0]["level"] == "warning"


# @spec WHOOP-AUTH-REFRESH-014, WHOOP-AUTH-REFRESH-016
def test_after_an_uncertain_failure_only_a_scheduled_unit_refreshes(
    folder: Path, clock: FakeClock, whoop: FakeWhoop
) -> None:
    write_token(folder, clock, expires_at=minutes(clock, -1), refresh_failed_at=minutes(clock, -9))
    with pytest.raises(WhoopAuthError):
        manager(folder, "retry").access_token()
    assert refreshes(whoop) == 0
    assert manager(folder, "schedule").access_token() == "AT-1"
    doc = read_token(folder)
    assert "refresh_failed_at" not in doc
    assert doc["refreshed_at"].startswith(clock.utc_now().strftime("%Y-%m-%dT%H:%M"))


# @spec WHOOP-AUTH-REFRESH-016
@pytest.mark.parametrize("failure", ["status", "timeout"])
def test_an_uncertain_failure_is_recorded(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, failure: str
) -> None:
    import httpx

    write_token(folder, clock, refreshed_at=minutes(clock, -45))
    answer = reply(502) if failure == "status" else httpx.ReadTimeout("slow")
    whoop.script("/oauth/oauth2/token", answer)
    with pytest.raises(WhoopAuthError):
        manager(folder).access_token()
    doc = read_token(folder)
    assert doc["refresh_failed_at"] and doc["refresh_token"] == "RT-0"


def _hold_token_lock(folder: Path, then: dict, held: threading.Event) -> None:
    fd = os.open(folder / "token.json.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    held.set()
    time.sleep(0.3)
    current = json.loads((folder / "token.json").read_text())
    (folder / "token.json").write_text(json.dumps({**current, **then}))
    os.close(fd)


# @spec WHOOP-AUTH-REFRESH-002
@pytest.mark.parametrize("other", ["refreshed", "still_stale"])
def test_the_token_is_re_read_under_the_lock(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, other: str
) -> None:
    write_token(folder, clock, refreshed_at=minutes(clock, -45))
    then = (
        {"access_token": "AT-OTHER", "refresh_token": "RT-OTHER", "refreshed_at": minutes(clock, 0)}
        if other == "refreshed"
        else {"refresh_token": "RT-REREAD"}
    )
    held = threading.Event()
    holder = threading.Thread(target=_hold_token_lock, args=(folder, then, held))
    holder.start()
    held.wait(5)
    got = manager(folder).access_token()
    holder.join()
    if other == "refreshed":
        assert (got, refreshes(whoop)) == ("AT-OTHER", 0)
    else:
        assert whoop.token_forms()[0]["refresh_token"] == "RT-REREAD"
