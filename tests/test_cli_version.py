"""`wearstream version` and `--help` (the parts of the CLI the scaffold delivers)."""

from __future__ import annotations

import importlib.metadata
import runpy
import subprocess
import sys

import pytest

from wearstream.cli import main

VERSION = importlib.metadata.version("wearstream")


# @spec OPERATOR-CMD-003
def test_version_prints_the_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == VERSION


# @spec OPERATOR-SET-009
def test_version_never_fails_on_configuration_or_environment() -> None:
    env = {
        "PATH": "/usr/bin:/bin",
        "WEARSTREAM_NOT_A_SETTING": "x",
        "WHOOP_CLIENT_SECRET": "x",
        "WEARSTREAM_SOURCES": "nonsense",
    }
    result = subprocess.run(
        [sys.executable, "-m", "wearstream", "version"], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0
    assert result.stdout.strip() == VERSION


# @spec OPERATOR-CMD-001
@pytest.mark.parametrize("argv", [["--help"], ["version", "--help"]])
def test_help_is_available(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 0
    assert "wearstream" in capsys.readouterr().out.lower()


def test_no_command_prints_help_and_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "<command>" in capsys.readouterr().out


def test_python_dash_m_runs_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["wearstream", "version"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("wearstream", run_name="__main__")
    assert exit_info.value.code == 0
