"""Names in the capture tree: validation, extensions, and parsing a file name back."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

NAME_RULE = "[a-z0-9_-]+"
EXT_RULE = r"[a-z0-9]+(\.[a-z0-9]+)*"
SIDECAR_EXT = "meta.json"
_NAME = re.compile(NAME_RULE)
_EXT = re.compile(EXT_RULE)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_TAIL = re.compile(r"(?P<ms>\d+)_(?P<id>[0-9a-f]{6})\.(?P<ext>.+)")
_CONTENT_TYPE_EXT = {
    "application/json": "json",
    "text/json": "json",
    "text/csv": "csv",
    "application/csv": "csv",
    "application/xml": "xml",
    "text/xml": "xml",
    "text/plain": "txt",
}


def valid_name(value: object) -> bool:
    return isinstance(value, str) and _NAME.fullmatch(value) is not None


def valid_day(value: object) -> bool:
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def valid_ext(value: object) -> bool:
    return (
        isinstance(value, str)
        and _EXT.fullmatch(value) is not None
        and value != SIDECAR_EXT
        and not value.endswith("." + SIDECAR_EXT)
    )


# @spec CAPTURE-NAME-001, CAPTURE-NAME-002
def name_problem(source: object, collection: object, day: object, ext: object) -> str | None:
    """Which name is invalid and the rule it breaks, never the value; None when all are valid."""
    for field, value in (("source", source), ("collection", collection)):
        if not valid_name(value):
            return f"{field} must match {NAME_RULE}"
    if not valid_day(day):
        return "dt must be a real calendar date written YYYY-MM-DD"
    if not valid_ext(ext):
        return f"ext must match {EXT_RULE} and must not be or end in .{SIDECAR_EXT}"
    return None


# @spec CAPTURE-WRITE-003
def ext_for(content_type: object) -> str:
    """The stored extension for a content type: parameters dropped, trimmed, lowercased."""
    if not isinstance(content_type, str):
        return "bin"
    return _CONTENT_TYPE_EXT.get(content_type.split(";")[0].strip().lower(), "bin")


@dataclass(frozen=True)
class ParsedName:
    stem: str
    ms: int
    is_sidecar: bool


def parse_name(name: str, collection: str) -> ParsedName | None:
    """`{collection}_{ms}_{id}.{ext}` for this folder's collection, parsed from the right."""
    prefix = collection + "_"
    if not name.startswith(prefix):
        return None
    tail = _TAIL.fullmatch(name[len(prefix) :])
    if tail is None:
        return None
    ext = tail["ext"]
    if ext != SIDECAR_EXT and not valid_ext(ext):
        return None
    stem = f"{collection}_{tail['ms']}_{tail['id']}"
    return ParsedName(stem, int(tail["ms"]), ext == SIDECAR_EXT)
