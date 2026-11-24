"""Stopping a process whose configuration is invalid, and applying the umask."""

from __future__ import annotations

import os

from wearstream.settings.load import Loaded

_ACTION = "Fix these settings and start again; `wearstream config` lists every problem."


# @spec OPERATOR-VAL-001, OPERATOR-VAL-002
def ensure_valid(loaded: Loaded, command: str, log_format: str | None = None) -> None:
    """Return when the configuration is valid; otherwise log `config_invalid` and exit.

    Called before the process takes any lock or writes under `/state`. Exits 2, or 1 for
    `healthcheck`, because Docker reserves 2 for health checks.
    """
    if not loaded.problems:
        return
    from wearstream import log

    fmt = log_format or loaded.values.get("WEARSTREAM_LOG_FORMAT", "console")
    log.configure(fmt=fmt, level="INFO")
    log.get_logger("operator").error("config_invalid", problems=loaded.problems, action=_ACTION)
    raise SystemExit(1 if command == "healthcheck" else 2)


# @spec OPERATOR-SET-010
def apply_umask(value: str) -> None:
    """Set the process's file-creation mask from a validated `WEARSTREAM_UMASK` value."""
    os.umask(int(value, 8))
