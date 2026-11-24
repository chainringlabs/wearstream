"""`wearstream status` as plain text, in the order the operator design gives."""

from __future__ import annotations

from typing import Any


def _daemon(daemon: dict[str, Any]) -> list[str]:
    if daemon["running"]:
        line = (
            f"Daemon: running (phase {daemon['phase']}, version {daemon['version']}, "
            f"heartbeat {daemon['heartbeat_age_seconds']} s ago)"
        )
    else:
        line = f"Daemon: not running ({daemon['heartbeat']} heartbeat)"
    lines = [line]
    if daemon["pause"]:
        lines.append(f"  capture paused: {daemon['pause'].get('reason')}")
    return lines


def _attention(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["Needs attention: nothing"]
    return ["Needs attention:"] + [f"  - {i['message']}. {i['action']}" for i in items]


def _state(source: dict[str, Any]) -> str:
    if source["state"] == "waiting_for_auth":
        return f"waiting for a credential since {source['since']}"
    if source["state"] == "rate_limited":
        return f"rate-limited until {source['cooldown_until']}"
    return "active"


def _source(name: str, source: dict[str, Any]) -> list[str]:
    success = source["last_source_success_at"] or "never"
    lines = [f"{name}: {_state(source)}; last successful unit {success}"]
    for job, info in source["jobs"].items():
        flag = ", FAILING" if info["failing"] else ""
        lines.append(
            f"  {job}: last completed tick {info['last_completed_tick'] or 'never'}, "
            f"{info['last_tick_outcome'] or 'no outcome yet'}{flag}"
        )
    for unit in source["in_flight"]:
        day = f" {unit['day']}" if unit.get("day") else ""
        lines.append(f"  in flight: {unit.get('job')}{day} (run {unit.get('run_id')})")
    for job, fill in source["backfill"].items():
        counts = fill["counts"]
        where = f"pass at {fill['position']}" if fill["in_progress"] else "no pass running"
        lines.append(
            f"  backfill {job}: {where}; missing {counts.get('missing', 0)}, filled "
            f"{counts.get('filled', 0)}, known empty {counts.get('known_empty', 0)}, given up "
            f"{counts.get('given_up', 0)}; {fill['needs_refill']} days need a refill"
        )
    return lines


def _checks(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["Checks: none warning, failing or suppressed"]
    lines = ["Checks:"]
    for row in rows:
        state = f"suppressed ({row['suppressed']})" if row["suppressed"] else row["severity"]
        accepted = ", accepted" if row["accepted"] else ""
        lines.append(f"  {row['check']}: {state}, {row['status']} since {row['since']}{accepted}")
    return lines


def render(report: dict[str, Any]) -> str:
    lines = _daemon(report["daemon"]) + _attention(report["needs_attention"])
    for name, source in report["sources"].items():
        lines += _source(name, source)
    return "\n".join(lines + _checks(report["checks"]))
