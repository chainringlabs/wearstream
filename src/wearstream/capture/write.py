"""`capture`: write one payload and its sidecar into the capture tree, atomically and
append-only."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from wearstream import __version__
from wearstream.capture import fsio, index
from wearstream.capture.names import SIDECAR_EXT, ext_for, name_problem, valid_day, valid_name
from wearstream.log import get_logger

NAME_ATTEMPTS = 5
SCHEMA_VERSION = "v1"


class Outcome(StrEnum):
    WRITTEN = "written"
    SKIPPED_IDENTICAL = "skipped_identical"
    SKIPPED_NON_2XX = "skipped_non_2xx"
    FAILED = "failed"


@dataclass(frozen=True)
class CaptureResult:
    outcome: Outcome
    path: str | None = None
    error_kind: str | None = None


class CaptureRefused(Exception):
    """A guard refused the payload; the message names what is wrong, never the value."""


def utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


def new_short_id() -> str:
    return secrets.token_hex(3)


def _fetched_at(ms: int) -> str:
    moment = dt.datetime.fromtimestamp(ms // 1000, dt.UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms % 1000:03d}Z"


@dataclass(frozen=True)
class _Call:
    root: Path
    source: str
    collection: str
    day: str
    ext: str
    ms: int

    @property
    def folder(self) -> Path:
        return self.root / self.source / self.collection / f"dt={self.day}"


def _log_context(source: object, collection: object, day: object) -> dict[str, object]:
    """The folder fields, with any invalid name replaced so its value is never logged."""
    return {
        "source": source if valid_name(source) else "(invalid)",
        "collection": collection if valid_name(collection) else "(invalid)",
        "dt": day if valid_day(day) else "(invalid)",
    }


# @spec CAPTURE-GUARD-001, CAPTURE-GUARD-002, CAPTURE-GUARD-004
def _check_payload(payload: object, meta: object) -> int | None:
    """Type and status guards; returns a refused 2xx-outside status, raises on bad input."""
    if not isinstance(payload, bytes | bytearray):
        raise CaptureRefused(f"payload must be bytes or bytearray, not {type(payload).__name__}")
    if not isinstance(meta, dict):
        raise CaptureRefused(f"meta must be a dict, not {type(meta).__name__}")
    status = meta.get("http_status")
    if status is None:
        return None
    if isinstance(status, bool) or not isinstance(status, int):
        raise CaptureRefused(f"http_status must be an integer, not {type(status).__name__}")
    return None if 200 <= status <= 299 else status


# @spec CAPTURE-SIDECAR-001, CAPTURE-SIDECAR-002, CAPTURE-SIDECAR-003
def _sidecar(call: _Call, payload: bytes, sha256: str, meta: dict[str, Any]) -> bytes:
    sidecar = {
        **meta,
        "source": call.source,
        "collection": call.collection,
        "fetched_at": _fetched_at(call.ms),
        "fetched_at_unix_ms": call.ms,
        "byte_size": len(payload),
        "sha256": sha256,
        "stored_encoding": "identity",
        "schema_version": SCHEMA_VERSION,
        "processor": "wearstream",
        "processor_version": __version__,
    }
    return json.dumps(sidecar, separators=(",", ":"), sort_keys=True, default=str).encode()


def _link_pair(call: _Call, payload_tmp: Path, sidecar_tmp: Path, log: Any) -> tuple[Path, str]:
    """Hard-link both files into place under a fresh name; retry a taken name five times."""
    for _ in range(NAME_ATTEMPTS):
        stem = f"{call.collection}_{call.ms}_{new_short_id()}"
        payload_path = call.folder / f"{stem}.{call.ext}"
        try:
            os.link(payload_tmp, payload_path)
        except FileExistsError:
            continue
        try:
            os.link(sidecar_tmp, call.folder / f"{stem}.{SIDECAR_EXT}")
        except FileExistsError:
            fsio.remove_quietly(payload_path)
            continue
        except BaseException:
            fsio.remove_quietly(payload_path)
            raise
        try:
            fsio.fsync_folder(call.folder)
        except OSError as exc:
            log.warning("capture_folder_sync_failed", error=fsio.describe_error(exc))
        return payload_path, stem
    raise FileExistsError(f"no free capture name after {NAME_ATTEMPTS} attempts")


# @spec CAPTURE-WRITE-004, CAPTURE-WRITE-006, CAPTURE-WRITE-007, CAPTURE-WRITE-008
# @spec CAPTURE-WRITE-009, CAPTURE-WRITE-011, CAPTURE-WRITE-012, CAPTURE-WRITE-014
def _write(call: _Call, payload: bytes, sidecar: bytes, log: Any) -> tuple[Path, str]:
    """Prepare and sync both temporary files, link them, and always remove the temporaries."""
    fsio.make_folders(call.root, call.folder)
    temporaries: list[Path] = []
    try:
        temporaries.append(fsio.write_temporary(call.folder, payload))
        temporaries.append(fsio.write_temporary(call.folder, sidecar))
        return _link_pair(call, temporaries[0], temporaries[1], log)
    finally:
        for path in temporaries:
            fsio.remove_quietly(path)


# @spec CAPTURE-DEDUPE-005
def _capture(
    call: _Call, payload: bytes, meta: dict[str, Any], dedupe: bool, scope: str, log: Any
) -> CaptureResult:
    sha256 = hashlib.sha256(payload).hexdigest()
    params = index.canonical(meta.get("request_params"))
    if dedupe:
        known = index.entries(call.root, call.source, call.collection, call.day)
        if index.earlier_sha256(known, scope, params) == sha256:
            log.debug("capture_skipped_identical")
            return CaptureResult(Outcome.SKIPPED_IDENTICAL)
    path, stem = _write(call, payload, _sidecar(call, payload, sha256, meta), log)
    index.add(
        call.root,
        call.source,
        call.collection,
        call.day,
        index.Entry(call.ms, stem, sha256, params),
    )
    log.debug("capture_written", path=str(path), byte_size=len(payload))
    return CaptureResult(Outcome.WRITTEN, str(path))


# @spec CAPTURE-WRITE-001, CAPTURE-WRITE-002, CAPTURE-WRITE-010, CAPTURE-WRITE-013
# @spec CAPTURE-GUARD-003, CAPTURE-LOG-001, CAPTURE-LOG-003, CAPTURE-LOG-004
def capture(
    root: Path | str,
    source: str,
    collection: str,
    payload: bytes | bytearray,
    meta: dict[str, Any],
    *,
    dt: str | None = None,
    dedupe: bool = True,
    dedupe_scope: str = "folder",
    ext: str | None = None,
) -> CaptureResult:
    """Write `payload` and its sidecar under `root`; never raises an ordinary error."""
    ms = utc_now_ms()
    day = dt or _fetched_at(ms)[:10]
    log = get_logger("capture").bind(**_log_context(source, collection, day))
    try:
        if ext is None:
            ext = ext_for(meta.get("content_type") if isinstance(meta, dict) else None)
        problem = name_problem(source, collection, day, ext)
        if problem:
            raise CaptureRefused(problem)
        status = _check_payload(payload, meta)
        if status is not None:
            log.warning("capture_skipped_non_2xx", http_status=status)
            return CaptureResult(Outcome.SKIPPED_NON_2XX)
        call = _Call(Path(root), source, collection, day, ext, ms)
        return _capture(call, bytes(payload), meta, dedupe, dedupe_scope, log)
    except Exception as exc:
        kind = fsio.error_kind(exc)
        error = str(exc) if isinstance(exc, CaptureRefused) else fsio.describe_error(exc)
        log.warning("capture_failed", error=error, error_kind=kind)
        return CaptureResult(Outcome.FAILED, error_kind=kind)
