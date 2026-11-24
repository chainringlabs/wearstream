"""What capture logs, and what it never logs (CAPTURE-LOG)."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest
from capture_helpers import DAY, Logs

from wearstream import log
from wearstream.capture import capture

SECRETISH = {
    "http_status": 200,
    "content_type": "application/json",
    "request_url": "https://api.example.test/v2/sleep?marker=URL-MARKER",
    "request_params": {"cursor": "PARAM-MARKER"},
}


# @spec CAPTURE-LOG-001
def test_written_skipped_and_failed_are_logged_with_their_folder(
    root: Path, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = capture(root, "whoop", "sleep", b"{}", SECRETISH, dt=DAY)
    capture(root, "whoop", "sleep", b"{}", SECRETISH, dt=DAY)
    monkeypatch.setattr(
        os, "link", lambda *_a, **_k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space"))
    )
    capture(root, "whoop", "sleep", b"new", SECRETISH, dt=DAY)
    [ok] = logs.events("capture_written")
    assert (ok["level"], ok["path"], ok["byte_size"]) == ("debug", written.path, 2)
    [same] = logs.events("capture_skipped_identical")
    assert same["level"] == "debug"
    [bad] = logs.events("capture_failed")
    assert (bad["level"], bad["error_kind"]) == ("warning", "environment")
    for event in (ok, same, bad):
        assert (event["source"], event["collection"], event["dt"]) == ("whoop", "sleep", DAY)


# @spec CAPTURE-LOG-002
def test_errors_carry_the_type_and_a_truncated_message(
    root: Path, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    raised = OSError(errno.EIO, "m" * 1000)

    def failing(*_args: object, **_kw: object) -> None:
        raise raised

    monkeypatch.setattr(os, "link", failing)
    capture(root, "whoop", "sleep", b"{}", SECRETISH, dt=DAY)
    error = logs.events("capture_failed")[0]["error"]
    assert error == "OSError: " + str(raised)[:300]


# @spec CAPTURE-LOG-002
def test_a_name_failure_names_the_field_and_rule_but_not_the_value(root: Path, logs: Logs) -> None:
    capture(root, "whoop", "VALUE-MARKER", b"{}", SECRETISH, dt=DAY)
    error = logs.events("capture_failed")[0]["error"]
    assert "collection" in error and "[a-z0-9_-]+" in error
    assert "VALUE-MARKER" not in b"".join(logs.lines).decode()


# @spec CAPTURE-LOG-003
def test_payloads_and_request_details_never_reach_the_log(
    root: Path, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b'{"PAYLOAD-MARKER":1}'
    capture(root, "whoop", "sleep", payload, SECRETISH, dt=DAY)
    capture(root, "whoop", "sleep", payload, SECRETISH, dt=DAY)
    capture(root, "whoop", "sleep", payload, {**SECRETISH, "http_status": 500}, dt=DAY)
    monkeypatch.setattr(
        os, "link", lambda *_a, **_k: (_ for _ in ()).throw(OSError(errno.EIO, "x"))
    )
    capture(root, "whoop", "sleep", b"other", SECRETISH, dt=DAY)
    text = b"".join(logs.lines).decode()
    assert len(logs.lines) >= 4
    for marker in ("PAYLOAD-MARKER", "URL-MARKER", "PARAM-MARKER"):
        assert marker not in text


# @spec CAPTURE-LOG-004
@pytest.mark.parametrize("fmt", ["json", "console"])
def test_paths_with_control_characters_stay_on_one_line(tmp_path: Path, fmt: str) -> None:
    lines: list[bytes] = []
    log.configure(fmt=fmt, level="DEBUG", write=lines.append)
    root = tmp_path / "da\nta\x1b[31m"
    root.mkdir()
    capture(root, "whoop", "sleep", b"{}", SECRETISH, dt=DAY)
    [line] = lines
    assert line.count(b"\n") == 1 and b"\x1b" not in line
