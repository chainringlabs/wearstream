"""Content health: sampled payloads carry data and match their sidecars
(checks design, *Content health*)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from wearstream.capture import StoredCapture
from wearstream.checks.payloads import EMPTY_CLASSES, classify_json, read_bytes, trailing
from wearstream.checks.result import Result
from wearstream.checks.settings import CheckSetting

SAMPLES = 5
# The warning reported when several samples disagree: the first of these found.
WARNING_ORDER = (
    "READER_MISMATCH",
    "ZERO_BYTES",
    "HTTP_ERROR",
    "ERROR_LIKE",
    "UNPARSEABLE",
    "EMPTY_LIST",
    "EMPTY_OBJECT",
    "EMPTY_WRAPPER",
    "NULL",
)
CORRUPTED_ACTION = (
    "The payload on disk no longer matches what was captured, and it won't be re-captured on its "
    "own because skip-identical matches its sidecar: restore it from a backup, or move the "
    "payload and its sidecar aside on the host."
)
READER_ACTION = "Correct the source's check settings: the collection's reader doesn't match."


# @spec CHECKS-CONTENT-001
def sample(captures: list[StoredCapture]) -> list[StoredCapture]:
    """The newest capture plus an even spread of the rest in name order: the same files while
    nothing changes."""
    if not captures:
        return []
    newest = max(captures, key=lambda c: (c.ms, c.stem))
    rest = sorted((c for c in captures if c is not newest), key=lambda c: c.payload.name)
    picks = SAMPLES - 1
    if len(rest) > picks:
        rest = [rest[int(i * (len(rest) - 1) / (picks - 1) + 0.5)] for i in range(picks)]
    return [newest, *rest]


def _corrupted(c: StoredCapture, body: bytes) -> bool:
    if hashlib.sha256(body).hexdigest() != c.meta.get("sha256"):
        return True
    size = c.meta.get("byte_size")
    return (isinstance(size, int) and size != len(body)) or (not body and _ext(c) == "json")


def _ext(c: StoredCapture) -> str:
    return c.payload.suffix.lstrip(".")


def classify(c: StoredCapture, body: bytes, reader: str) -> str:
    if _ext(c) != reader:
        return "READER_MISMATCH"
    if not body:
        return "ZERO_BYTES"
    status = c.meta.get("http_status")
    if isinstance(status, int) and not isinstance(status, bool) and not 200 <= status <= 299:
        return "HTTP_ERROR"
    if _ext(c) != "json":
        return "DATA"
    try:
        return classify_json(json.loads(body))
    except ValueError:
        return "UNPARSEABLE"


# @spec CHECKS-CONTENT-002, CHECKS-CONTENT-003, CHECKS-CONTENT-004, CHECKS-CONTENT-005
# @spec CHECKS-CONTENT-006
def content_health(data: Path, s: CheckSetting) -> Result:
    """Verify and classify up to five sampled captures."""
    picked = sample([c for _day, found in trailing(data, s) for c in found])
    corrupted, problems = [], []
    for c in picked:
        body = read_bytes(c)
        if body is None or _corrupted(c, body):
            corrupted.append(str(c.payload))
            continue
        kind = classify(c, body, s.reader)
        if kind != "DATA" and not (s.expected_empty and kind in EMPTY_CLASSES):
            problems.append((str(c.payload), kind))
    details: dict = {"sampled": len(picked)}
    if corrupted:
        return Result("error", "corrupted", {**details, "files": corrupted}, CORRUPTED_ACTION)
    if not problems:
        return Result("pass", "ok" if picked else "no_captures", details)
    kinds = [kind for _path, kind in problems]
    first = next(k for k in WARNING_ORDER if k in kinds)
    details.update(files=[path for path, _kind in problems], classes=kinds)
    if first == "READER_MISMATCH":
        details["setting"] = f"reader={s.reader}"
        return Result("warning", "reader_mismatch", details, READER_ACTION)
    status = "empty" if first in EMPTY_CLASSES else first.lower()
    return Result("warning", status, details)
