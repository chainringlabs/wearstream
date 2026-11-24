"""Reading the tree back: what counts as a capture, in what order, and whether a day has data."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wearstream.capture.names import parse_name, valid_day, valid_name


@dataclass(frozen=True)
class StoredCapture:
    stem: str
    ms: int
    payload: Path
    sidecar: Path
    meta: dict[str, Any]


@dataclass
class _Stem:
    ms: int
    payloads: list[Path] = field(default_factory=list)
    sidecars: list[Path] = field(default_factory=list)


def folder_for(root: Path, source: str, collection: str, day: str) -> Path:
    return Path(root) / source / collection / f"dt={day}"


def group_stems(folder: Path, collection: str) -> dict[str, _Stem]:
    """Name-conforming files of a folder by stem; dotfiles, strangers and subfolders skipped."""
    stems: dict[str, _Stem] = {}
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_file(follow_symlinks=False):
                continue
            parsed = parse_name(entry.name, collection)
            if parsed is None:
                continue
            group = stems.setdefault(parsed.stem, _Stem(parsed.ms))
            (group.sidecars if parsed.is_sidecar else group.payloads).append(Path(entry.path))
    return stems


def _sidecar_meta(path: Path, source: str, collection: str) -> dict[str, Any] | None:
    try:
        meta = json.loads(path.read_bytes())
    except OSError, ValueError:
        return None
    if not isinstance(meta, dict) or not isinstance(meta.get("sha256"), str):
        return None
    if meta.get("source") != source or meta.get("collection") != collection:
        return None
    return meta


# @spec CAPTURE-READ-001, CAPTURE-READ-002, CAPTURE-READ-003
def list_captures(root: Path, source: str, collection: str, day: str) -> list[StoredCapture]:
    """The captures of one `(source, collection, dt)` folder, newest first."""
    if not (valid_name(source) and valid_name(collection) and valid_day(day)):
        return []
    try:
        stems = group_stems(folder_for(root, source, collection, day), collection)
    except FileNotFoundError, NotADirectoryError:
        return []
    found = []
    for stem, group in stems.items():
        if len(group.payloads) != 1 or len(group.sidecars) != 1:
            continue
        meta = _sidecar_meta(group.sidecars[0], source, collection)
        if meta is not None:
            found.append(StoredCapture(stem, group.ms, group.payloads[0], group.sidecars[0], meta))
    return sorted(found, key=lambda c: (c.ms, c.stem), reverse=True)


# @spec CAPTURE-READ-004
def has_data(root: Path, source: str, collection: str, day: str) -> bool:
    """Whether the folder holds at least one capture."""
    return bool(list_captures(root, source, collection, day))
