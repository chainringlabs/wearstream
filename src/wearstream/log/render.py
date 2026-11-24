"""Turning an event into one log line: redaction, console and JSON formats, the 4 KiB cap."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

LIMIT = 4096  # bytes per line, newline included
ENVELOPE = ("timestamp", "level", "event", "component")
CONTEXT = ("run_id", "source", "job", "trigger", "day", "dt")
PROTECTED = frozenset((*ENVELOPE, *CONTEXT, "action", "truncated"))
REDACTED = "[redacted]"
_CREDENTIAL_WORDS = (
    "password", "passwd", "secret", "token", "apikey", "api_key", "authorization", "credential",
    "private_key", "privatekey", "signature", "sessionid", "session_id", "cookie",
)  # fmt: skip
_CONTROL_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}

Event = dict[str, Any]


def _is_credential(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in _CREDENTIAL_WORDS)


# @spec OPERATOR-REDACT-001
def redact(value: Any) -> Any:
    """Replace the value of every credential-named field, at any depth, with `[redacted]`."""
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_credential(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


def order(event: Event) -> Event:
    """Envelope first, then the unit's context, then the event's own fields."""
    ordered = {key: event[key] for key in (*ENVELOPE, *CONTEXT) if key in event}
    ordered.update((key, value) for key, value in event.items() if key not in ordered)
    return ordered


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int | float):
        return str(value)
    return _compact(value)


def _escape_controls(text: str) -> str:
    out = []
    for char in text:
        if char in _CONTROL_ESCAPES:
            out.append(_CONTROL_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\x{ord(char):02x}")
        else:
            out.append(char)
    return "".join(out)


def _console_value(value: Any) -> str:
    text = _as_text(value)
    plain = text and not any(c in text for c in ' "=\\') and text == _escape_controls(text)
    if plain:
        return text
    return '"' + _escape_controls(text.replace("\\", "\\\\").replace('"', '\\"')) + '"'


# @spec OPERATOR-LOG-003
def console_line(event: Event) -> bytes:
    """`TIMESTAMP LEVEL event key=value ...`, with no color codes."""
    head = [
        str(event.get("timestamp", "")),
        str(event.get("level", "")).upper(),
        str(event["event"]),
    ]
    fields = [
        f"{key}={_console_value(value)}"
        for key, value in event.items()
        if key not in ("timestamp", "level", "event")
    ]
    return (" ".join(head + fields) + "\n").encode()


def json_line(event: Event) -> bytes:
    return (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode()


def _longest(event: Event, skip: frozenset[str] | tuple[str, ...]) -> str | None:
    candidates = [(len(_as_text(v)), k) for k, v in event.items() if k not in skip]
    candidates = [(size, key) for size, key in candidates if size > 3]
    return max(candidates)[1] if candidates else None


# @spec OPERATOR-LOG-001
def fit(event: Event, render: Callable[[Event], bytes]) -> bytes:
    """Render `event` in at most 4 KiB, shortening its longest unprotected values first."""
    line = render(event)
    if len(line) <= LIMIT:
        return line
    event = {**event, "truncated": True}
    for _ in range(64):
        line = render(event)
        over = len(line) - LIMIT
        if over <= 0:
            return line
        key = _longest(event, PROTECTED) or _longest(event, (*ENVELOPE, "truncated"))
        if key is None:
            break
        text = _as_text(event[key])
        event[key] = text[: max(0, len(text) - over - 8)] + "..."
    return line[: LIMIT - 1] + b"\n"
