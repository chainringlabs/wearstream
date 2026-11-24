"""Configuring the process's logger: format, level and where lines are written."""

from __future__ import annotations

import datetime as dt
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from wearstream.log.render import console_line, fit, json_line, order, redact


def _write_stdout(line: bytes) -> None:
    """One unbuffered write to stdout per line (a short write is finished, never interleaved)."""
    view = memoryview(line)
    while view:
        view = view[os.write(1, view) :]


@dataclass
class _State:
    write: Callable[[bytes], None] = _write_stdout
    fmt: str = "console"
    debug: bool = False


STATE = _State()


def _timestamp() -> str:
    now = dt.datetime.now(dt.UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _envelope(_logger: Any, method: str, event: dict[str, Any]) -> dict[str, Any]:
    level = {"warn": "warning", "exception": "error", "critical": "error"}.get(method, method)
    event.setdefault("component", "wearstream")
    return order({"timestamp": _timestamp(), "level": level, **event})


def _render(_logger: Any, _method: str, event: dict[str, Any]) -> bytes:
    renderer = json_line if STATE.fmt == "json" else console_line
    return fit(redact(event), renderer)


class _Sink:
    """The logger structlog hands each finished line to."""

    def msg(self, line: bytes) -> None:
        STATE.write(line)

    debug = info = warning = warn = error = critical = exception = fatal = msg


# @spec OPERATOR-LOG-001, OPERATOR-LOG-004
def configure(
    fmt: str = "console", level: str = "INFO", write: Callable[[bytes], None] | None = None
) -> None:
    """Set the format (`console` or `json`), the level (`INFO` or `DEBUG`) and the writer."""
    STATE.write = write or _write_stdout
    STATE.fmt = fmt.lower()
    STATE.debug = level.upper() == "DEBUG"
    structlog.configure(
        processors=[_envelope, _render],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if STATE.debug else logging.INFO
        ),
        logger_factory=lambda *_args: _Sink(),
        cache_logger_on_first_use=False,
    )


def get_logger(component: str) -> Any:
    """A logger whose every line carries `component`; `.bind()` adds a unit's context."""
    return structlog.get_logger(component=component)
