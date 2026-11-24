"""Reading captures for the checks: the trailing folders, parsed payloads, records and keys."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from wearstream import capture
from wearstream.capture import StoredCapture
from wearstream.checks.settings import CheckSetting

DATA_ARRAY_KEYS = ("records", "data", "items", "results", "activities")
ERROR_KEYS = ("error", "errors", "message", "fault", "exception", "status_code")
REAL_ERROR_KEYS = ("error", "errors", "fault", "exception")
PAGINATION_KEYS = ("next_token", "nextToken", "next", "paging")
EMPTY_CLASSES = ("EMPTY_LIST", "EMPTY_OBJECT", "EMPTY_WRAPPER", "NULL")
UNREADABLE = object()


def trailing(data: Path, s: CheckSetting) -> list[tuple[str, list[StoredCapture]]]:
    """The collection's `recent_folders` newest `dt=` folders holding a capture, newest first."""
    found: list[tuple[str, list[StoredCapture]]] = []
    for day in capture.list_days(data, s.source, s.collection):
        captures = capture.list_captures(data, s.source, s.collection, day)
        if captures:
            found.append((day, captures))
            if len(found) == s.recent_folders:
                break
    return found


def read_bytes(c: StoredCapture) -> bytes | None:
    try:
        return c.payload.read_bytes()
    except OSError:
        return None


def classify_json(obj: Any) -> str:
    if obj is None:
        return "NULL"
    if isinstance(obj, list):
        return "DATA" if obj else "EMPTY_LIST"
    if not isinstance(obj, dict):
        return "DATA"
    if not obj:
        return "EMPTY_OBJECT"
    for key in DATA_ARRAY_KEYS:
        if obj.get(key) == [] and all(k in (key, *PAGINATION_KEYS) for k in obj):
            return "EMPTY_WRAPPER"
    lower = {k.lower() for k in obj}
    has_data = any(isinstance(obj.get(k), list) and obj[k] for k in DATA_ARRAY_KEYS)
    if not has_data and any(k in lower for k in REAL_ERROR_KEYS):
        return "ERROR_LIKE"
    return "DATA"


def parse(c: StoredCapture, reader: str) -> Any:
    """The payload as the reader sees it: a JSON value, CSV rows, text, or UNREADABLE."""
    body = read_bytes(c)
    if body is None:
        return UNREADABLE
    try:
        if reader == "json":
            return json.loads(body)
        text = body.decode("utf-8")
    except ValueError:
        return UNREADABLE
    if reader == "csv":
        return list(csv.reader(io.StringIO(text)))
    return text


def is_empty(value: Any, reader: str) -> bool:
    if value is UNREADABLE:
        return True
    if reader == "json":
        return classify_json(value) in EMPTY_CLASSES
    if reader == "csv":
        return len(value) < 2
    return not value.strip()


def records(value: Any, s: CheckSetting) -> list[dict[str, Any]]:
    """The JSON objects whose keys and dates count: the wrapper's records, the object, or the
    object elements of a list; for CSV, each row by column name."""
    if s.reader == "csv":
        header, *rows = value
        return [dict(zip(header, row, strict=False)) for row in rows]
    if s.unnest_records:
        value = value.get("records") if isinstance(value, dict) else None
        value = value if isinstance(value, list) else []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def keys_of(value: Any, s: CheckSetting) -> set[str]:
    """The signature of one readable, non-empty payload (CHECKS-SCHEMA-001)."""
    if s.reader == "csv":
        return set(value[0])
    if s.reader == "txt":
        first = value.splitlines()[0] if value.splitlines() else ""
        return {f"fields={len(first.split())}"}
    return {key for record in records(value, s) for key in record}


def lookup(record: dict[str, Any], dotted: str) -> Any:
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value
