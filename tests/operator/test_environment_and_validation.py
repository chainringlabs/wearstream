"""Paths, refused environment, validation and `wearstream config`
(OPERATOR-PATH, -ENV, -VAL, -CONFIG)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wearstream.cli import main
from wearstream.settings import ensure_valid, load, paths

BASE = {"WEARSTREAM_SOURCES": "whoop"}


def _dirs(tmp_path: Path) -> dict[str, str]:
    env = {}
    for name in ("data", "state", "secrets"):
        (tmp_path / name).mkdir()
        env[f"WEARSTREAM_{name.upper()}_DIR"] = str(tmp_path / name)
    return env


# @spec OPERATOR-PATH-001
def test_paths_default_to_the_container_mounts() -> None:
    resolved = paths(load(BASE, "daemon").values)
    assert (resolved.data, resolved.state, resolved.secrets) == (
        Path("/data"),
        Path("/state"),
        Path("/secrets"),
    )
    assert resolved.state_path("locks", "whoop.lock") == Path("/state/locks/whoop.lock")


# @spec OPERATOR-PATH-001
def test_paths_follow_the_variables(tmp_path: Path) -> None:
    resolved = paths(load({**BASE, **_dirs(tmp_path)}, "daemon").values)
    assert resolved.secrets_path("garmin", "email") == tmp_path / "secrets" / "garmin" / "email"


# @spec OPERATOR-PATH-002
def test_directories_must_be_distinct_and_not_nested(tmp_path: Path) -> None:
    env = {**BASE, **_dirs(tmp_path)}
    assert load(env, "daemon").problems == []
    (tmp_path / "state" / "data").mkdir()
    nested = {**env, "WEARSTREAM_DATA_DIR": str(tmp_path / "state" / "data")}
    assert any("inside" in p for p in load(nested, "daemon").problems)
    (tmp_path / "alias").symlink_to(tmp_path / "state")
    same = {**env, "WEARSTREAM_DATA_DIR": str(tmp_path / "alias")}
    assert any("same folder" in p for p in load(same, "daemon").problems)


# @spec OPERATOR-ENV-001
@pytest.mark.parametrize(
    "name",
    [
        "WHOOP_CLIENT_SECRET",
        "whoop_token_path",
        "GARMINCONNECT_PASSWORD",
        "WEARSTREAM_API_KEY",
        "GARMIN_PASSWD",
    ],
)
def test_credential_like_variables_are_refused(name: str) -> None:
    problems = load({**BASE, name: "x"}, "daemon").problems
    assert any(name in p and "remove" in p for p in problems)


# @spec OPERATOR-ENV-001
def test_wearstream_settings_and_garmintokens_are_not_refused(tmp_path: Path) -> None:
    env = {**BASE, **_dirs(tmp_path), "GARMINTOKENS": "/x", "GITHUB_TOKEN": "x"}
    assert load(env, "daemon").problems == []


# @spec OPERATOR-VAL-002
def test_every_problem_is_reported_together() -> None:
    env = {
        "WEARSTREAM_SOURCES": "fitbit",
        "WEARSTREAM_FAILING_TICKS": "0",
        "WHOOP_CLIENT_SECRET": "x",
    }
    assert len(load(env, "daemon").problems) == 3


# @spec OPERATOR-VAL-001, OPERATOR-VAL-002
def test_ensure_valid_logs_config_invalid_and_exits_2(capfd: pytest.CaptureFixture[str]) -> None:
    loaded = load({"WEARSTREAM_SOURCES": "fitbit"}, "daemon")
    with pytest.raises(SystemExit) as exit_info:
        ensure_valid(loaded, "daemon", log_format="json")
    assert exit_info.value.code == 2
    event = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert event["event"] == "config_invalid"
    assert event["level"] == "error"
    assert "wearstream config" in event["action"]
    assert any("fitbit" in p for p in event["problems"])


# @spec OPERATOR-VAL-002
def test_healthcheck_exits_1_on_invalid_configuration() -> None:
    loaded = load({"WEARSTREAM_LOG_FROMAT": "x"}, "healthcheck")
    with pytest.raises(SystemExit) as exit_info:
        ensure_valid(loaded, "healthcheck", log_format="json")
    assert exit_info.value.code == 1


# @spec OPERATOR-CONFIG-001
def test_config_prints_values_origins_and_secret_presence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {**BASE, **_dirs(tmp_path), "WEARSTREAM_LOG_FORMAT": "json"}
    (tmp_path / "secrets" / "whoop").mkdir()
    (tmp_path / "secrets" / "whoop" / "client_id").write_text("abc-not-shown")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert main(["config"]) == 0
    out = capsys.readouterr().out
    assert "WEARSTREAM_LOG_FORMAT = json (environment)" in out
    assert "WEARSTREAM_FAILING_TICKS = 3 (default)" in out
    assert "whoop/client_id: present" in out
    assert "whoop/client_secret: missing" in out
    assert "abc-not-shown" not in out


# @spec OPERATOR-CONFIG-001
def test_config_lists_problems_and_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WEARSTREAM_FAILING_TICKS", "0")
    assert main(["config"]) == 2
    assert "WEARSTREAM_FAILING_TICKS" in capsys.readouterr().out
