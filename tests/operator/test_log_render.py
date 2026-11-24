"""Log line shape, formats, size cap, reserved fields and redaction (OPERATOR-LOG, -REDACT)."""

from __future__ import annotations

import json
import re

import pytest

from wearstream import log

TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    written: list[bytes] = []
    return written


def _configure(lines: list[bytes], fmt: str, level: str = "INFO") -> None:
    log.configure(fmt=fmt, level=level, write=lines.append)


# @spec OPERATOR-LOG-001, OPERATOR-LOG-004
def test_every_line_carries_the_envelope(lines: list[bytes]) -> None:
    _configure(lines, "json")
    log.get_logger("supervisor").info("run_finished", written=3)
    assert len(lines) == 1 and lines[0].endswith(b"\n")
    event = json.loads(lines[0])
    assert TS.match(event["timestamp"])
    assert (event["level"], event["event"], event["component"], event["written"]) == (
        "info",
        "run_finished",
        "supervisor",
        3,
    )


# @spec OPERATOR-LOG-004
def test_unit_context_is_carried_on_every_line(lines: list[bytes]) -> None:
    _configure(lines, "json")
    run = log.get_logger("whoop").bind(
        run_id="r1", source="whoop", job="range", trigger="schedule", day="2026-11-23"
    )
    run.info("capture_written", dt="2026-11-23")
    event = json.loads(lines[0])
    for key in ("run_id", "source", "job", "trigger", "day", "dt"):
        assert key in event


# @spec OPERATOR-LOG-003
def test_console_format(lines: list[bytes]) -> None:
    _configure(lines, "console")
    log.get_logger("supervisor").error(
        "job_failing", action='read the "run_failed" lines', job="range"
    )
    line = lines[0].decode()
    assert re.match(
        r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z ERROR job_failing component=supervisor ", line
    )
    assert 'action="read the \\"run_failed\\" lines"' in line
    assert "job=range" in line
    assert "\x1b[" not in line


# @spec OPERATOR-LOG-003
def test_console_escapes_control_characters(lines: list[bytes]) -> None:
    _configure(lines, "console")
    log.get_logger("capture").warning("capture_failed", error="bad\nline\tand\x07bell")
    line = lines[0].decode()
    assert line.count("\n") == 1
    assert "\\n" in line and "\\t" in line and "\\x07" in line


# @spec OPERATOR-LOG-001
def test_debug_is_dropped_at_info(lines: list[bytes]) -> None:
    _configure(lines, "json", "INFO")
    log.get_logger("capture").debug("capture_written")
    assert lines == []
    _configure(lines, "json", "DEBUG")
    log.get_logger("capture").debug("capture_written")
    assert len(lines) == 1


# @spec OPERATOR-LOG-001
@pytest.mark.parametrize("fmt", ["json", "console"])
def test_long_events_are_cut_to_4_kib_keeping_the_envelope_and_action(
    lines: list[bytes], fmt: str
) -> None:
    _configure(lines, fmt)
    log.get_logger("checks").error(
        "check_failed",
        action="act now",
        source="garmin",
        details=["x" * 3000] * 4,
        other="y" * 2000,
    )
    line = lines[0]
    assert len(line) <= 4096
    text = line.decode()
    for kept in ("check_failed", "act now", "garmin", "checks"):
        assert kept in text
    if fmt == "json":
        event = json.loads(line)
        assert event["truncated"] is True
        assert event["action"] == "act now"
    else:
        assert "truncated=true" in text


# @spec OPERATOR-REDACT-001
def test_credential_named_fields_are_redacted_at_any_depth(lines: list[bytes]) -> None:
    _configure(lines, "json")
    log.get_logger("whoop").warning(
        "oops",
        Refresh_Token="r",
        details={"client_secret": "s", "nested": [{"Authorization": "a", "cookie": "c"}], "ok": 1},
        path="/secrets/whoop/token.json",
    )
    event = json.loads(lines[0])
    assert event["Refresh_Token"] == "[redacted]"
    assert event["details"]["client_secret"] == "[redacted]"
    assert event["details"]["nested"][0] == {"Authorization": "[redacted]", "cookie": "[redacted]"}
    assert event["details"]["ok"] == 1
    assert event["path"] == "/secrets/whoop/token.json"
    assert b'"r"' not in lines[0] and b'"s"' not in lines[0]
