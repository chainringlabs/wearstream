"""Cleaning up after an interrupted run: temporary files go, lone files are reported."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from wearstream.capture import fsio
from wearstream.capture.names import valid_day, valid_name
from wearstream.capture.read import folder_for, group_stems
from wearstream.log import get_logger


def _collections(root: Path, source: str, log: Any) -> list[str]:
    try:
        with os.scandir(root / source) as entries:
            return sorted(
                e.name for e in entries if e.is_dir(follow_symlinks=False) and valid_name(e.name)
            )
    except FileNotFoundError:
        return []
    except OSError as exc:
        log.warning("capture_repair_failed", error=fsio.describe_error(exc))
        return []


def _remove_temporaries(folder: Path) -> int:
    with os.scandir(folder) as entries:
        temporaries = [
            Path(e.path)
            for e in entries
            if e.name.startswith(".tmp_") and e.is_file(follow_symlinks=False)
        ]
    for path in temporaries:
        os.unlink(path)
    return len(temporaries)


def _orphans(folder: Path, collection: str) -> list[Path]:
    """Lone payloads and lone sidecars: stems with files of only one kind."""
    lone = []
    for group in group_stems(folder, collection).values():
        if group.payloads and not group.sidecars:
            lone.extend(group.payloads)
        elif group.sidecars and not group.payloads:
            lone.extend(group.sidecars)
    return sorted(lone)


def _repair_folder(root: Path, source: str, collection: str, day: str) -> None:
    log = get_logger("capture").bind(source=source, collection=collection, dt=day)
    folder = folder_for(root, source, collection, day)
    try:
        removed = _remove_temporaries(folder)
        orphans = _orphans(folder, collection)
    except FileNotFoundError:
        return
    except Exception as exc:
        log.warning("capture_repair_failed", error=fsio.describe_error(exc))
        return
    if removed:
        log.info("capture_tmp_removed", count=removed)
    for path in orphans:
        log.warning("capture_orphan_found", path=str(path))


# @spec CAPTURE-REPAIR-001, CAPTURE-REPAIR-002, CAPTURE-REPAIR-003, CAPTURE-REPAIR-004
def repair(root: Path | str, source: str, days: Iterable[object]) -> None:
    """Visit `dt={day}` of each of `source`'s collections for the given days; never raises."""
    root = Path(root)
    log = get_logger("capture").bind(source=source)
    if not valid_name(source):
        log.warning("capture_repair_failed", error="source must match [a-z0-9_-]+")
        return
    collections = _collections(root, source, log)
    for day in sorted({str(d) for d in days}):
        if not valid_day(day):
            continue
        for collection in collections:
            _repair_folder(root, source, collection, day)
