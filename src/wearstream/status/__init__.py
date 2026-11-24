"""`wearstream status`: what WearStream is doing and what needs a person (operator design)."""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

from wearstream import log
from wearstream.settings import ensure_valid, load, paths
from wearstream.status.gather import ACTIONS, SCHEMA_VERSION, gather, never_ran
from wearstream.status.render import render

NEVER_RAN = "WearStream has never run with this /state folder: it holds no state yet."


def _item(kind: str, message: str, action: str | None = None, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "message": message, "action": action or ACTIONS[kind], **fields}


def _source_items(name: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    if source["state"] == "waiting_for_auth":
        items.append(
            _item(
                "waiting_for_auth",
                f"{name} is waiting for a new credential",
                f"Run `wearstream auth {name}` (for example "
                f"`docker exec -it <container> wearstream auth {name}`).",
                source=name,
            )
        )
    if source["repeatedly_rate_limited"]:
        items.append(
            _item("repeatedly_rate_limited", f"{name} is repeatedly rate-limited", source=name)
        )
    for job, info in source["jobs"].items():
        if info["failing"]:
            items.append(
                _item("job_failing", f"the {name} {job} job is failing", source=name, job=job)
            )
    return items


# @spec OPERATOR-STATUS-003
def needs_attention(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Every condition `/state` records that needs a person, each with its action."""
    daemon, items = report["daemon"], []
    if not daemon["running"]:
        items.append(
            _item(
                "daemon_not_running", f"the daemon is not running ({daemon['heartbeat']} heartbeat)"
            )
        )
    if daemon["pause"]:
        items.append(_item("capture_paused", f"capture is paused: {daemon['pause'].get('reason')}"))
    for name, source in report["sources"].items():
        items += _source_items(name, source)
    for row in report["checks"]:
        if row["severity"] == "error" and not row["suppressed"] and not row["accepted"]:
            action = row["action"] or "Read this check's check_failed line."
            items.append(
                _item(
                    "check_failing",
                    f"{row['check']} is failing ({row['status']})",
                    action,
                    source=row["source"],
                    check=row["check"],
                )
            )
    for relative in report["unreadable"]:
        items.append(
            _item("unreadable_document", f"/state/{relative} can't be read", path=relative)
        )
    return items


# @spec OPERATOR-STATUS-001, OPERATOR-STATUS-002, OPERATOR-STATUS-004, OPERATOR-STATUS-005
def status_command(*, as_json: bool) -> int:
    """Read only `/state`; exit 0 when nothing needs attention, 1 when something does, 3 when
    `/state` holds no state."""
    loaded = load(os.environ, "status")
    ensure_valid(loaded, "status")
    values = loaded.values
    log.configure(fmt=values["WEARSTREAM_LOG_FORMAT"], level=values["WEARSTREAM_LOG_LEVEL"])
    state = paths(values).state
    if never_ran(state):
        print(
            json.dumps({"schema_version": SCHEMA_VERSION, "never_ran": True})
            if as_json
            else NEVER_RAN
        )
        return 3
    report = gather(state, dt.datetime.now(dt.UTC))
    report["needs_attention"] = needs_attention(report)
    print(json.dumps(report, indent=2, sort_keys=False) if as_json else render(report))
    return 1 if report["needs_attention"] else 0
