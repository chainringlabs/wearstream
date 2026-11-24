"""The credential files, token-store guards and the fingerprint (GARMIN-AUTH-STORE-001,
-GUARD-001..003, -FP-001, -FP-002)."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from garmin_helpers import EMAIL, PASSWORD, SESSION, FakeLibrary, Logs

from wearstream.garmin import GarminSource
from wearstream.garmin.store import read_credentials
from wearstream.settings import load, paths

Run = Callable[..., tuple[int, dict[str, Any]]]


# @spec GARMIN-AUTH-STORE-001
def test_credentials_drop_one_trailing_newline(folder: Path) -> None:
    (folder / "email").write_text(EMAIL + "\n")
    (folder / "password").write_text(" " + PASSWORD + "  \n")
    assert read_credentials(folder) == (EMAIL, " " + PASSWORD + "  ")
    (folder / "password").unlink()
    assert read_credentials(folder) == (EMAIL, None)


def unusable(logs: Logs) -> list[str]:
    events = logs.events("garmin_tokenstore_unusable")
    assert all(e["level"] == "error" and e["action"] and e["path"] for e in events)
    return [e["reason"] for e in events]


# @spec GARMIN-AUTH-GUARD-001
def test_a_healthy_store_logs_nothing(
    session: Path, garmin: FakeLibrary, run: Run, logs: Logs
) -> None:
    assert run()[0] == 0
    assert unusable(logs) == [] and logs.events("garmin_tokenstore_not_saved") == []


# @spec GARMIN-AUTH-GUARD-001
def test_an_unwritable_folder_is_logged_then_signed_in_anyway(
    session: Path, garmin: FakeLibrary, run: Run, logs: Logs, folder: Path
) -> None:
    folder.chmod(0o500)
    try:
        assert run()[0] == 0
    finally:
        folder.chmod(0o700)
    assert "not_writable" in unusable(logs)


# @spec GARMIN-AUTH-GUARD-001
def test_a_missing_folder_under_an_unwritable_parent(
    garmin: FakeLibrary, run: Run, logs: Logs, folder: Path, credentials: None
) -> None:
    for name in ("email", "password"):
        (folder / name).unlink()
    folder.rmdir()
    folder.parent.chmod(0o500)
    try:
        run()
    finally:
        folder.parent.chmod(0o700)
    assert unusable(logs) == ["parent_not_writable"]


# @spec GARMIN-AUTH-GUARD-001
def test_a_store_on_the_containers_own_layer_is_off_volume(
    session: Path, garmin: FakeLibrary, run: Run, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wearstream.garmin.store.is_mount", lambda _p: False)
    run()
    assert unusable(logs) == ["off_volume"]


# @spec GARMIN-AUTH-GUARD-001
def test_a_symlinked_store_is_rejected(
    env: dict[str, str], garmin: FakeLibrary, unit: Callable[..., Any], logs: Logs, tmp_path: Path
) -> None:
    from wearstream.runner.run import execute

    secrets = Path(env["WEARSTREAM_SECRETS_DIR"])
    (secrets / "garmin").rmdir()
    (tmp_path / "elsewhere").mkdir()
    (secrets / "garmin").symlink_to(tmp_path / "elsewhere")
    ctx = unit()
    execute(ctx, ctx.log)
    assert "path_rejected" in unusable(logs)


# @spec GARMIN-AUTH-GUARD-002
def test_a_session_that_was_not_saved_is_logged(
    credentials: None, garmin: FakeLibrary, run: Run, logs: Logs
) -> None:
    garmin.store_writable = False  # the library's save failed silently
    assert run()[0] == 0
    [event] = logs.events("garmin_tokenstore_not_saved")
    assert event["level"] == "error" and "writable" in event["action"]


# @spec GARMIN-AUTH-GUARD-002
def test_an_old_file_holding_the_same_session_counts_as_saved(
    session: Path, garmin: FakeLibrary, run: Run, logs: Logs
) -> None:
    old = session.stat().st_mtime - 3600
    os.utime(session, (old, old))
    assert run()[0] == 0
    assert logs.events("garmin_tokenstore_not_saved") == []


# @spec GARMIN-AUTH-GUARD-003, GARMIN-AUTH-LOG-001
def test_no_part_of_the_session_reaches_the_log(
    credentials: None, garmin: FakeLibrary, run: Run, logs: Logs
) -> None:
    garmin.store_writable = False
    run()
    for secret in (*SESSION.values(), PASSWORD):
        assert secret not in logs.text()


def fingerprint(env: dict[str, str]) -> str:
    return GarminSource().fingerprint(paths(load(env, "status").values))


# @spec GARMIN-AUTH-FP-001
def test_the_fingerprint_follows_metadata_never_contents(
    env: dict[str, str], folder: Path, session: Path, credentials: None
) -> None:
    first = fingerprint(env)
    assert fingerprint(env) == first
    before = session.stat()
    session.write_text(json.dumps({**SESSION, "di_token": "DI-TOKEN-2"}))  # same size
    os.utime(session, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert fingerprint(env) == first
    session.chmod(0)
    try:
        assert fingerprint(env) != first
    finally:
        session.chmod(stat.S_IMODE(before.st_mode))
    (folder / "password").write_text("another\n")
    assert fingerprint(env) != first


# @spec GARMIN-AUTH-FP-001
def test_a_rejected_store_path_still_has_a_fingerprint(env: dict[str, str], tmp_path: Path) -> None:
    secrets = Path(env["WEARSTREAM_SECRETS_DIR"])
    (secrets / "garmin").rmdir()
    (tmp_path / "elsewhere").mkdir()
    (secrets / "garmin").symlink_to(tmp_path / "elsewhere")
    assert len(fingerprint(env)) == 64


# @spec GARMIN-AUTH-FP-002
def test_a_needed_credential_reports_the_fingerprint(
    garmin: FakeLibrary, run: Run, env: dict[str, str]
) -> None:
    code, result = run()
    assert code == 4
    assert result["fingerprint"] == fingerprint(env)
