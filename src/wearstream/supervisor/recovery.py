"""Recovering units a stop cut off, and watching hand-started runs (supervisor design)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from wearstream import capture
from wearstream.state import parse_time, release, try_lock
from wearstream.state.refill import record_refill
from wearstream.supervisor.units import Repair, Unit, repair_days, run_fields, should_retry


def _months(now: dt.datetime) -> list[str]:
    previous = now.replace(day=1) - dt.timedelta(days=1)
    return [f"{previous:%Y-%m}", f"{now:%Y-%m}"]


class RecoveryMixin:
    # @spec SUPERVISOR-STATE-006
    def _inflight_entries(self: Any) -> list[dict[str, Any]]:
        path = self.store.path("inflight.json")
        doc = self.store.read_quiet("inflight.json")
        if doc is not None or not path.exists():
            return list((doc or {}).get("units", []))
        self.log.warning("state_document_reset", path=str(path), reason="unreadable")
        lines = self.store.run_lines(_months(self.clock.now()))
        finished = {line.get("run_id") for line in lines if line.get("type") == "finish"}
        starts = [
            line for line in lines if line.get("type") == "start" and line.get("origin") == "daemon"
        ]
        self._write("inflight.json", {"units": []})
        return [
            {**line, "started_at": line.get("at")}
            for line in starts
            if line.get("run_id") not in finished
        ]

    # @spec SUPERVISOR-LIFE-003
    def _recover(self: Any) -> None:
        now = self.clock.now()
        for entry in self._inflight_entries():
            key = (entry.get("source"), entry.get("job"))
            if entry.get("origin") != "daemon" or key not in self.jobs:
                continue
            job = self.jobs[key]
            unit = Unit(
                job.source,
                job.name,
                entry.get("day"),
                entry.get("trigger") or "schedule",
                entry.get("run_id") or "?",
            )
            outcome = "killed" if entry.get("signalled") == "limit" else "interrupted"
            self._append_run(
                {
                    "type": "finish",
                    "origin": "daemon",
                    **run_fields(unit),
                    "day": unit.day,
                    "outcome": outcome,
                    "exit_code": None,
                }
            )
            self.log.warning("inflight_recovered", **run_fields(unit), outcome=outcome)
            if job.kind == "checks":
                self._remove_inflight(unit.run_id)
                continue
            started = parse_time(entry.get("started_at")) or now
            days = repair_days(unit, job, started, self.previous_stop or now)
            self.sources[job.source].repairs.append(
                Repair(unit, job, days, should_retry(unit.trigger, outcome))
            )
            if unit.day:
                self._refill(unit, False)

    def _manual_finishes(self: Any) -> set[str]:
        lines = self.store.run_lines(_months(self.clock.now()))
        return {
            line["run_id"]
            for line in lines
            if line.get("type") == "finish" and line.get("origin") == "manual" and "run_id" in line
        }

    # @spec SUPERVISOR-MANUAL-005, SUPERVISOR-MANUAL-010
    def _minute_tasks(self: Any) -> None:
        mono = self.clock.monotonic()
        if mono - self._minute_mark < 60:
            return
        self._minute_mark = mono
        for entry in (self.store.read_quiet("inflight.json") or {}).get("units", []):
            if entry.get("origin") == "manual" and entry.get("source") in self.sources:
                self._recover_manual(entry)
        lines = self.store.run_lines(_months(self.clock.now()))
        for line in lines:
            if (
                line.get("type") == "finish"
                and line.get("origin") == "manual"
                and line.get("run_id") not in self._reported_manual
            ):
                self._reported_manual.add(line.get("run_id"))
                self.log.info(
                    "manual_run_finished",
                    run_id=line.get("run_id"),
                    source=line.get("source"),
                    job=line.get("job"),
                    day=line.get("day"),
                    outcome=line.get("outcome"),
                )

    def _recover_manual(self: Any, entry: dict[str, Any]) -> None:
        key = (entry.get("source"), entry.get("job"))
        runtime = self.sources[entry["source"]]
        if key not in self.jobs or runtime.running:
            return
        fd = try_lock(self.paths.state_path("locks", f"{entry['source']}.lock"))
        if fd is None:
            return
        try:
            job, now = self.jobs[key], self.clock.now()
            unit = Unit(
                job.source, job.name, entry.get("day"), "manual", entry.get("run_id") or "?"
            )
            days = repair_days(unit, job, parse_time(entry.get("started_at")) or now, now)
            capture.repair(self.paths.data, job.source, days)
            common = {"origin": "manual", **run_fields(unit), "day": unit.day}
            self._append_run(
                {"type": "finish", **common, "outcome": "interrupted", "exit_code": None}
            )
            self._append_run({"type": "repaired", **common, "days": days})
            self._reported_manual.add(unit.run_id)
            self.log.warning("inflight_recovered", **run_fields(unit), outcome="interrupted")
            self._remove_inflight(unit.run_id)
            if unit.day:
                record_refill(self.store, job.source, job.name, unit.day, False)
        finally:
            release(fd)
