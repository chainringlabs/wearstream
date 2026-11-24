"""Schema drift: the payload's top-level keys against the accepted baseline
(checks design, *Schema drift*)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wearstream.checks.payloads import is_empty, keys_of, parse, trailing
from wearstream.checks.result import Result
from wearstream.checks.settings import CheckSetting

SIGNATURE_VERSION = 1  # bump when the signature rules change; baselines are then re-recorded


def baseline_path(state: Path, source: str, collection: str) -> Path:
    return Path(state) / "checks" / "baselines" / source / f"{collection}.json"


def baseline_doc(keys: set[str]) -> dict[str, Any]:
    return {"version": SIGNATURE_VERSION, "keys": sorted(keys)}


# @spec CHECKS-SCHEMA-001
def current_keys(data: Path, s: CheckSetting) -> set[str] | None:
    """The union of the keys of each trailing folder's newest readable, non-empty payload."""
    found: set[str] | None = None
    for _day, captures in trailing(data, s):
        for c in captures:
            value = parse(c, s.reader)
            if is_empty(value, s.reader):
                continue
            keys = keys_of(value, s)
            if keys:
                found = (found or set()) | keys
                break
    return found


def _read_baseline(path: Path) -> dict[str, Any] | None | bool:
    """The baseline document, None when absent, or False when it can't be read."""
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_bytes())
    except OSError, ValueError:
        return False
    if not isinstance(doc, dict) or not isinstance(doc.get("keys", []), list):
        return False
    return doc


def _accept_action(s: CheckSetting) -> str:
    return f"wearstream accept-schema {s.source} {s.collection}"


# @spec CHECKS-SCHEMA-002, CHECKS-SCHEMA-003, CHECKS-SCHEMA-004, CHECKS-SCHEMA-005
# @spec CHECKS-SCHEMA-006
def schema_drift(data: Path, state: Path, s: CheckSetting) -> Result:
    """Compare the current keys with the baseline; never writes (the unit records a baseline)."""
    keys = current_keys(data, s)
    if keys is None:
        return Result("pass", "no_payload")
    stored = _read_baseline(baseline_path(state, s.source, s.collection))
    if stored is False:
        return Result(
            "error",
            "baseline_unreadable",
            {},
            "The accepted schema can't be read; accept the current one with "
            f"`{_accept_action(s)}`.",
        )
    if not stored or not stored.get("keys") or stored.get("version") != SIGNATURE_VERSION:
        return Result("pass", "baseline_set", {"keys": sorted(keys)}, baseline=baseline_doc(keys))
    accepted = {str(k) for k in stored["keys"]}
    new, missing = sorted(keys - accepted), sorted(accepted - keys)
    if new:
        action = (
            f"If the change is expected, accept it with `{_accept_action(s)}`; "
            "tools that read /data may need updating."
        )
        return Result("error", "drift", {"new_keys": new, "missing_keys": missing}, action)
    if missing:
        return Result("warning", "missing", {"missing_keys": missing})
    return Result("pass", "ok")
