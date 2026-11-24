"""Helpers shared by the checks tests: placing captures in the tree at chosen times, building
check settings, a log collector, and `/state` documents."""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

from wearstream.checks import CheckSetting
from wearstream.state import iso

NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=dt.UTC)  # a Tuesday, noon UTC
_IDS = itertools.count(1)


def ms(moment: dt.datetime) -> int:
    return int(moment.timestamp() * 1000)


def hours_ago(hours: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=hours)


def put(
    data: Path,
    collection: str,
    day: str,
    payload: Any,
    *,
    source: str = "whoop",
    at: dt.datetime | None = None,
    ext: str = "json",
    meta: dict[str, Any] | None = None,
) -> Path:
    """Write one capture (payload and sidecar) as the capture component would; returns the
    payload's path. A non-bytes payload is JSON-encoded."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    moment = at or dt.datetime.fromisoformat(f"{day}T06:00:00+00:00")
    stem = f"{collection}_{ms(moment)}_{next(_IDS):06x}"
    folder = data / source / collection / f"dt={day}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.{ext}").write_bytes(body)
    sidecar = {
        "source": source,
        "collection": collection,
        "fetched_at": iso(moment),
        "fetched_at_unix_ms": ms(moment),
        "byte_size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "http_status": 200,
        **(meta or {}),
    }
    (folder / f"{stem}.meta.json").write_text(json.dumps(sidecar))
    return folder / f"{stem}.{ext}"


def setting(collection: str = "sleep", *families: str, **fields: Any) -> CheckSetting:
    return CheckSetting(
        source=fields.pop("source", "whoop"),
        collection=collection,
        families=families or ("freshness",),
        **fields,
    )


def days_back(count: int, *, step: int = 1, end: dt.date | None = None) -> list[str]:
    """`count` ISO days ending at `end` (default: NOW's date), `step` days apart, oldest first."""
    last = end or NOW.date()
    return [(last - dt.timedelta(days=i * step)).isoformat() for i in range(count)][::-1]


def write_state(state: Path, relative: str, doc: Any) -> None:
    path = state / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))


def read_state(state: Path, relative: str) -> Any:
    return json.loads((state / relative).read_text())


def heartbeat(state: Path, *, age: float = 10, pause: Any = None, **sources: str) -> None:
    write_state(
        state,
        "heartbeat.json",
        {
            "pid": 1,
            "version": "0.0.0",
            "phase": "running",
            "updated_at": iso(NOW - dt.timedelta(seconds=age)),
            "pause": pause,
            "sources": sources or {"whoop": "active"},
        },
    )


class Logs:
    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        parsed = [json.loads(line) for line in self.lines]
        return [e for e in parsed if name is None or e["event"] == name]

    def names(self) -> list[str]:
        return [e["event"] for e in self.events()]
