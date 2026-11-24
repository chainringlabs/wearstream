"""Routing everything a process might print into its logger, and quieting libraries."""

from __future__ import annotations

import atexit
import logging
import sys
import threading
import warnings
from typing import Any, TextIO

from wearstream.log.errors import exception_fields
from wearstream.log.setup import get_logger

BUG_ACTION = (
    "This is a WearStream bug: report it at https://github.com/chainringlabs/wearstream/issues "
    "with the log lines around it."
)
QUIET_LIBRARIES = ("httpx", "httpcore", "curl_cffi")
GARMIN_LIBRARY = "garminconnect"


class _StderrToLog:
    """A stand-in for sys.stderr that logs each complete line as a `stderr` event."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self) -> None:
        self._pending = ""

    def write(self, text: str) -> int:
        self._pending += text
        *lines, self._pending = self._pending.split("\n")
        for line in lines:
            get_logger("process").warning("stderr", text=line)
        return len(text)

    def flush(self) -> None:
        if self._pending:
            line, self._pending = self._pending, ""
            get_logger("process").warning("stderr", text=line)

    def isatty(self) -> bool:
        return False


class _LibraryHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        method = "debug"
        for threshold, name in (
            (logging.ERROR, "error"),
            (logging.WARNING, "warning"),
            (logging.INFO, "info"),
        ):
            if record.levelno >= threshold:
                method = name
                break
        getattr(get_logger("library"), method)(
            "library_log", logger=record.name, message=record.getMessage()
        )


def _route_library_logs() -> None:
    root = logging.getLogger()
    if not any(isinstance(handler, _LibraryHandler) for handler in root.handlers):
        root.addHandler(_LibraryHandler())


def _show_warning(message: Warning | str, category: type[Warning], *_args: Any, **_kw: Any) -> None:
    get_logger("process").warning(
        "python_warning", category=category.__name__, message=str(message)[:300]
    )


def _log_uncaught(exc: BaseException) -> None:
    get_logger("process").error("uncaught_exception", action=BUG_ACTION, **exception_fields(exc))


# @spec OPERATOR-LOG-002
def route_process_output(stderr: TextIO | None = None) -> None:
    """Send stderr, warnings, uncaught exceptions and library logs to the logger."""
    replacement = stderr or _StderrToLog()
    sys.stderr = replacement
    atexit.register(replacement.flush)
    warnings.showwarning = _show_warning
    sys.excepthook = lambda _type, exc, _tb: _log_uncaught(exc)
    threading.excepthook = lambda args: _log_uncaught(args.exc_value) if args.exc_value else None
    _route_library_logs()


# @spec OPERATOR-LOG-008
def quiet_libraries(garmin_debug: bool = False) -> None:
    """HTTP libraries log at WARNING and above; garminconnect only when its debug is on."""
    _route_library_logs()
    for name in QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger(GARMIN_LIBRARY).setLevel(
        logging.DEBUG if garmin_debug else logging.CRITICAL + 1
    )


# @spec OPERATOR-LOG-009
def startup_warnings(values: dict[str, Any]) -> None:
    """Warnings the daemon repeats on every start while a risky setting is on."""
    if values.get("WEARSTREAM_GARMIN_LIBRARY_DEBUG"):
        get_logger("operator").warning(
            "library_debug_enabled",
            message="Garmin library output may include request details and tokens.",
            action="Turn WEARSTREAM_GARMIN_LIBRARY_DEBUG off once troubleshooting is done.",
        )
