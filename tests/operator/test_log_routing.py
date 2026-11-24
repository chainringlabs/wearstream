"""stderr, warnings and uncaught-exception routing; exception rendering; library loggers
(OPERATOR-LOG-002, -006, -007, -008, -009, -010)."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import textwrap
import warnings

import pytest

from wearstream import log


def _events(lines: list[bytes]) -> list[dict]:
    return [json.loads(line) for line in lines]


@pytest.fixture
def lines() -> list[bytes]:
    written: list[bytes] = []
    log.configure(fmt="json", level="DEBUG", write=written.append)
    return written


def _child(code: str) -> list[dict]:
    """Run code in a fresh interpreter whose first act is to route output, as every process does."""
    prelude = (
        "from wearstream import log\n"
        "log.configure(fmt='json', level='INFO')\n"
        "log.route_process_output()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(code)], capture_output=True, text=True
    )
    assert result.stderr == "", result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


# @spec OPERATOR-LOG-002
def test_stderr_writes_become_stderr_events() -> None:
    events = _child("import sys\nsys.stderr.write('raw problem\\n')\n")
    assert [(e["event"], e["text"]) for e in events] == [("stderr", "raw problem")]


# @spec OPERATOR-LOG-002
def test_warnings_become_python_warning_events() -> None:
    events = _child("import warnings\nwarnings.warn('old api', DeprecationWarning, stacklevel=1)\n")
    assert events[0]["event"] == "python_warning"
    assert events[0]["category"] == "DeprecationWarning"


# @spec OPERATOR-LOG-002
def test_uncaught_exceptions_become_an_error_with_an_action() -> None:
    events = _child("raise RuntimeError('boom')\n")
    event = events[-1]
    assert (event["event"], event["level"]) == ("uncaught_exception", "error")
    assert event["error_type"] == "RuntimeError"
    assert event["action"]


# @spec OPERATOR-LOG-002, OPERATOR-LOG-008
def test_library_loggers_are_routed_and_quieted(lines: list[bytes]) -> None:
    log.quiet_libraries(garmin_debug=False)
    logging.getLogger("httpx").info("chatty")
    logging.getLogger("httpx").warning("worth seeing")
    logging.getLogger("garminconnect").error("never shown")
    events = _events(lines)
    assert [(e["event"], e["logger"], e["message"]) for e in events] == [
        ("library_log", "httpx", "worth seeing")
    ]


# @spec OPERATOR-LOG-006, OPERATOR-LOG-007
def test_ordinary_exceptions_log_type_and_a_truncated_message(lines: list[bytes]) -> None:
    try:
        raise ValueError("m" * 500)
    except ValueError as exc:
        fields = log.exception_fields(exc)
    assert fields["error_type"] == "ValueError"
    assert len(fields["error"]) == 300


# @spec OPERATOR-LOG-007
def test_exceptions_from_sources_and_http_libraries_log_only_their_type() -> None:
    namespace: dict = {"__name__": "httpx._client"}
    exec("def call():\n    raise RuntimeError('Authorization: Bearer abc')\n", namespace)
    try:
        namespace["call"]()
    except RuntimeError as exc:
        fields = log.exception_fields(exc)
    assert fields["error_type"] == "RuntimeError"
    assert "error" not in fields


# @spec OPERATOR-LOG-006, OPERATOR-LOG-010
def test_debug_tracebacks_show_frames_and_types_but_no_messages_or_locals(
    lines: list[bytes],
) -> None:
    secret = "s3cr3t-local"  # noqa: F841 - must never be rendered
    try:
        try:
            raise KeyError("inner message")
        except KeyError as inner:
            raise ValueError("outer message") from inner
    except ValueError as exc:
        log.get_logger("operator").error("failed", action="x", **log.exception_fields(exc))
    event = _events(lines)[-1]
    traceback_text = event["traceback"]
    assert "ValueError" in traceback_text and "KeyError" in traceback_text
    assert "test_log_routing.py" in traceback_text
    for leaked in ("outer message", "inner message", "s3cr3t-local"):
        assert leaked not in traceback_text
    assert "s3cr3t-local" not in json.dumps(event)


# @spec OPERATOR-LOG-010
def test_no_traceback_at_info() -> None:
    written: list[bytes] = []
    log.configure(fmt="json", level="INFO", write=written.append)
    try:
        raise ValueError("x")
    except ValueError as exc:
        assert "traceback" not in log.exception_fields(exc)


# @spec OPERATOR-LOG-009
def test_library_debug_warns_at_start(lines: list[bytes]) -> None:
    log.startup_warnings({"WEARSTREAM_GARMIN_LIBRARY_DEBUG": True})
    event = _events(lines)[-1]
    assert (event["event"], event["level"]) == ("library_debug_enabled", "warning")
    assert "token" in event["message"]
    lines.clear()
    log.startup_warnings({"WEARSTREAM_GARMIN_LIBRARY_DEBUG": False})
    assert lines == []


def test_routing_is_never_installed_in_the_test_process() -> None:
    assert not warnings.showwarning.__module__.startswith("wearstream")


# @spec OPERATOR-LOG-002
def test_unfinished_stderr_lines_and_thread_exceptions_are_logged() -> None:
    events = _child(
        "import sys, threading\n"
        "t = threading.Thread(target=lambda: 1 / 0)\nt.start(); t.join()\n"
        "sys.stderr.write('no newline')\n"
    )
    assert events[0]["event"] == "uncaught_exception"
    assert events[0]["error_type"] == "ZeroDivisionError"
    assert (events[-1]["event"], events[-1]["text"]) == ("stderr", "no newline")


# @spec OPERATOR-LOG-002
def test_the_executable_routes_output_before_running(monkeypatch: pytest.MonkeyPatch) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv = ['wearstream', 'nonsense']\n"
            "from wearstream.cli import entry\nentry()",
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "WEARSTREAM_LOG_FORMAT": "json"},
    )
    assert result.returncode == 2
    assert result.stderr == ""
    assert any(
        json.loads(line)["event"] == "stderr"
        for line in result.stdout.splitlines()
        if line.startswith("{")
    )
