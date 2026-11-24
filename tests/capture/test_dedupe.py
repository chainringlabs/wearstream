"""Skip-identical by folder and by request, canonical parameters, the in-process index
(CAPTURE-DEDUPE)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from capture_helpers import DAY, put, put_capture

from wearstream.capture import capture, clear_index, list_captures


def _cap(root: Path, payload: bytes, params: Any = None, **kw: Any) -> str:
    meta = {"http_status": 200, "content_type": "application/json", "request_params": params}
    return capture(root, "garmin", "activities", payload, meta, dt=kw.pop("dt", DAY), **kw).outcome


def _count(root: Path, day: str = DAY) -> int:
    return len(list_captures(root, "garmin", "activities", day))


# @spec CAPTURE-DEDUPE-001
def test_folder_scope_skips_bytes_equal_to_the_newest_capture(root: Path) -> None:
    assert _cap(root, b"A") == "written"
    assert _cap(root, b"A") == "skipped_identical"
    assert _cap(root, b"B") == "written"
    assert _cap(root, b"A") == "written"
    assert _cap(root, b"A", dt="2026-11-21") == "written"
    assert _count(root) == 3


# @spec CAPTURE-DEDUPE-002
def test_request_scope_compares_only_with_the_same_requests_captures(root: Path) -> None:
    assert _cap(root, b"X", {"id": 1}, dedupe_scope="request") == "written"
    assert _cap(root, b"Y", {"id": 2}, dedupe_scope="request") == "written"
    assert _cap(root, b"X", {"id": 1}, dedupe_scope="request") == "skipped_identical"
    assert _cap(root, b"X", {"id": 2}, dedupe_scope="request") == "written"
    assert _cap(root, b"X", {"id": 2}) == "skipped_identical"


# @spec CAPTURE-DEDUPE-003
def test_request_parameters_compare_in_canonical_json(root: Path) -> None:
    assert _cap(root, b"X", {"b": 1, "a": [1, 2]}, dedupe_scope="request") == "written"
    assert _cap(root, b"X", {"a": [1, 2], "b": 1}, dedupe_scope="request") == "skipped_identical"
    assert _cap(root, b"Z", {"day": dt.date(2026, 1, 1)}, dedupe_scope="request") == "written"
    assert _cap(root, b"Z", {"day": "2026-01-01"}, dedupe_scope="request") == "skipped_identical"


# @spec CAPTURE-DEDUPE-003
def test_sidecars_from_an_adopted_tree_match(root: Path) -> None:
    folder = root / "garmin" / "activities" / f"dt={DAY}"
    meta = {
        "source": "garmin", "collection": "activities", "sha256": hashlib.sha256(b"X").hexdigest(),
        "request_params": {"start": 0, "limit": 20},
    }  # fmt: skip
    put_capture(folder, "activities_100_abcdef", meta, b"X")
    assert (
        _cap(root, b"X", {"limit": 20, "start": 0}, dedupe_scope="request") == "skipped_identical"
    )


# @spec CAPTURE-DEDUPE-004
def test_without_a_matching_capture_the_payload_is_written(root: Path) -> None:
    folder = root / "garmin" / "activities" / f"dt={DAY}"
    put(
        folder,
        "activities_100_abcdef.meta.json",
        json.dumps(
            {
                "source": "garmin",
                "collection": "activities",
                "sha256": hashlib.sha256(b"X").hexdigest(),
            }
        ),
    )
    assert _cap(root, b"X") == "written"
    assert _cap(root, b"X", {"id": 9}, dedupe_scope="request") == "written"


# @spec CAPTURE-DEDUPE-005
def test_dedupe_off_keeps_every_capture(root: Path) -> None:
    for _ in range(3):
        assert _cap(root, b"A", dedupe=False) == "written"
    assert _count(root) == 3


# @spec CAPTURE-DEDUPE-006
def test_a_folder_is_indexed_once_per_process(root: Path) -> None:
    assert _cap(root, b"A") == "written"
    folder = root / "garmin" / "activities" / f"dt={DAY}"
    newer = {
        "source": "garmin",
        "collection": "activities",
        "sha256": hashlib.sha256(b"B").hexdigest(),
    }
    put_capture(folder, "activities_99999999999999_abcdef", newer, b"B")
    assert _cap(root, b"A") == "skipped_identical"
    clear_index()
    assert _cap(root, b"A") == "written"
