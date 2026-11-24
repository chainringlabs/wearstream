"""What the sidecar records and how it is serialized (CAPTURE-SIDECAR)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from capture_helpers import DAY, META

from wearstream import __version__
from wearstream.capture import capture

FETCHED_AT = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


def _sidecar(result: Any) -> tuple[Path, dict[str, Any]]:
    path = Path(result.path)
    sidecar = path.with_name(path.name.removesuffix(".json") + ".meta.json")
    return sidecar, json.loads(sidecar.read_bytes())


# @spec CAPTURE-SIDECAR-001
def test_the_writer_sets_provenance_and_overrides_caller_values(root: Path) -> None:
    payload = b'{"score":87}'
    caller = {
        **META,
        "source": "garmin",
        "collection": "other",
        "sha256": "forged",
        "byte_size": 1,
        "fetched_at": "never",
        "stored_encoding": "gzip",
        "schema_version": "v9",
        "processor": "someone-else",
        "processor_version": "0",
    }
    _, meta = _sidecar(capture(root, "whoop", "sleep", payload, caller, dt=DAY))
    assert meta["source"] == "whoop" and meta["collection"] == "sleep"
    assert meta["sha256"] == hashlib.sha256(payload).hexdigest()
    assert meta["byte_size"] == len(payload)
    assert (meta["stored_encoding"], meta["schema_version"]) == ("identity", "v1")
    assert (meta["processor"], meta["processor_version"]) == ("wearstream", __version__)
    assert FETCHED_AT.match(meta["fetched_at"])
    assert isinstance(meta["fetched_at_unix_ms"], int)


# @spec CAPTURE-SIDECAR-001
def test_fetched_at_and_the_name_agree_on_the_fetch_time(root: Path) -> None:
    result = capture(root, "whoop", "sleep", b"{}", META, dt=DAY)
    _, meta = _sidecar(result)
    ms = meta["fetched_at_unix_ms"]
    assert Path(result.path).name.split("_")[-2] == str(ms)
    moment = dt.datetime.fromtimestamp(ms // 1000, dt.UTC)
    rendered = moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms % 1000:03d}Z"
    assert meta["fetched_at"] == rendered


# @spec CAPTURE-SIDECAR-002
def test_caller_keys_pass_through_unchanged(root: Path) -> None:
    caller = {
        **META,
        "request_params": {"limit": 25, "nested": {"b": [1, 2]}},
        "charset": "utf-8",
        "content_encoding": "gzip",
        "capture_mode": "trailing_window",
        "redacted_fields": ["profile.accessToken"],
    }
    _, meta = _sidecar(capture(root, "whoop", "sleep", b"{}", caller, dt=DAY))
    for key, value in caller.items():
        assert meta[key] == value


# @spec CAPTURE-SIDECAR-003
def test_the_sidecar_is_compact_json_with_sorted_keys(root: Path) -> None:
    sidecar, meta = _sidecar(capture(root, "whoop", "sleep", b"{}", META, dt=DAY))
    assert sidecar.read_bytes() == json.dumps(meta, separators=(",", ":"), sort_keys=True).encode()
