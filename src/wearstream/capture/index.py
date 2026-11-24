"""The in-process index skip-identical compares against."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wearstream.capture.read import folder_for, list_captures


@dataclass(frozen=True)
class Entry:
    ms: int
    stem: str
    sha256: str
    params: str


_INDEX: dict[Path, list[Entry]] = {}


# @spec CAPTURE-DEDUPE-003
def canonical(value: Any) -> str:
    """The fixed form `request_params` are compared in (it must match existing sidecars)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# @spec CAPTURE-DEDUPE-006
def entries(root: Path, source: str, collection: str, day: str) -> list[Entry]:
    """The folder's captures, newest first, read once per process and extended in place."""
    folder = folder_for(root, source, collection, day)
    if folder not in _INDEX:
        _INDEX[folder] = [
            Entry(c.ms, c.stem, c.meta["sha256"], canonical(c.meta.get("request_params")))
            for c in list_captures(root, source, collection, day)
        ]
    return _INDEX[folder]


def add(root: Path, source: str, collection: str, day: str, entry: Entry) -> None:
    folder_entries = entries(root, source, collection, day)
    folder_entries.append(entry)
    folder_entries.sort(key=lambda e: (e.ms, e.stem), reverse=True)


# @spec CAPTURE-DEDUPE-001, CAPTURE-DEDUPE-002, CAPTURE-DEDUPE-004
def earlier_sha256(folder_entries: list[Entry], scope: str, params: str) -> str | None:
    """The sha256 skip-identical compares with: the newest capture the scope selects."""
    if scope not in ("folder", "request"):
        raise ValueError(f"dedupe_scope must be folder or request, not {scope!r}")
    for entry in folder_entries:
        if scope == "folder" or entry.params == params:
            return entry.sha256
    return None


def clear_index() -> None:
    """Forget every indexed folder (the next capture into a folder re-reads it)."""
    _INDEX.clear()
