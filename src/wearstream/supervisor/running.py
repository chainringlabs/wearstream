"""Starting units, watching their time limits, and what follows when one ends."""

from __future__ import annotations

from typing import Any

import wearstream.supervisor as pkg
from wearstream import capture
from wearstream.state import iso, release, try_lock
from wearstream.state.refill import record_refill
from wearstream.supervisor.units import (
    COUNT_KEYS,
    Repair,
    Running,
    Unit,
    classify,
    repair_days,
    run_fields,
    should_retry,
    valid_result,
)

TERM, KILL = 15, 9
_KILLED_ACTION = {
    True: "A check is hanging: see the last check this unit logged before it was stopped.",
    False: "Check the network and the vendor's status; "
    "if runs are legitimately slow, raise this job's time limit.",
}


class RunningMixin:
    def _source_can_start(self: Any, runtime: Any) -> bool:
        now = self.clock.now()
        return not (
            self.pause.active
            or runtime.state != "active"
            or runtime.cooling(now)
            or runtime.repairs
        )

    # @spec SUPERVISOR-QUEUE-001, SUPERVISOR-QUEUE-002, SUPERVISOR-PAUSE-002
    def _start_units(self: Any) -> None:
        for runtime in self.sources.values():
            if runtime.checks_running is None and runtime.checks_queue:
                self._start(runtime, runtime.checks_queue.popleft(), checks=True)
            if runtime.running is None and runtime.queue and self._source_can_start(runtime):
                self._start(runtime, runtime.queue.popleft(), checks=False)

    # @spec SUPERVISOR-STATE-003, SUPERVISOR-CHILD-001
    def _start(self: Any, runtime: Any, unit: Unit, checks: bool) -> None:
        job, now = self.jobs[unit.source, unit.job], self.clock.now()
        entry = {
            "run_id": unit.run_id,
            "source": unit.source,
            "job": unit.job,
            "day": unit.day,
            "trigger": unit.trigger,
            "started_at": iso(now),
            "origin": "daemon",
        }
        added = self._update(
            "inflight.json", lambda doc: {"units": [*(doc or {}).get("units", []), entry]}
        )
        if added is None:
            (runtime.checks_queue if checks else runtime.queue).appendleft(unit)
            return
        self._append_run({"type": "start", "origin": "daemon", **run_fields(unit), "day": unit.day})
        handle = self._spawn(unit, job)
        running = Running(unit, job, handle, self.clock.monotonic(), now)
        if checks:
            runtime.checks_running = running
        else:
            runtime.running = running
        self.log.info("run_started", **run_fields(unit))

    def _poll_units(self: Any) -> None:
        for runtime in self.sources.values():
            for slot in ("running", "checks_running"):
                running = getattr(runtime, slot)
                if running is None:
                    continue
                code = running.handle.poll()
                if code is None:
                    self._enforce_limit(running)
                    continue
                setattr(runtime, slot, None)
                self._finish(runtime, running, code)

    # @spec SUPERVISOR-LIMIT-001, SUPERVISOR-LIMIT-002
    def _enforce_limit(self: Any, running: Running) -> None:
        mono = self.clock.monotonic()
        if running.signalled is None and mono - running.started_mono > running.job.time_limit:
            running.signalled, running.term_mono = "limit", mono
            running.handle.signal(TERM)
            run_id = running.unit.run_id

            def mark(doc: dict | None) -> dict:
                units = [
                    {**u, "signalled": "limit"} if u.get("run_id") == run_id else u
                    for u in (doc or {}).get("units", [])
                ]
                return {"units": units}

            self._update("inflight.json", mark)
        elif (
            running.signalled == "limit"
            and not running.killed
            and mono - (running.term_mono or mono) > pkg.STOP_GRACE_SECONDS
        ):
            running.killed = True
            running.handle.signal(KILL)

    def _remove_inflight(self: Any, run_id: str) -> None:
        self._update(
            "inflight.json",
            lambda doc: {
                "units": [u for u in (doc or {}).get("units", []) if u.get("run_id") != run_id]
            },
        )

    # @spec SUPERVISOR-AFTER-001, SUPERVISOR-AFTER-003, SUPERVISOR-AFTER-006, SUPERVISOR-MANUAL-004
    def _finish(self: Any, runtime: Any, running: Running, code: int) -> None:
        unit, job, now = running.unit, running.job, self.clock.now()
        outcome = classify(code, running.signalled)
        result = valid_result(running.handle.result())
        duration = round(self.clock.monotonic() - running.started_mono, 3)
        counts = {k: result[k] for k in COUNT_KEYS} if result else None
        error = result.get("error") if result else None
        self._append_run(
            {
                "type": "finish",
                "origin": "daemon",
                **run_fields(unit),
                "day": unit.day,
                "outcome": outcome,
                "exit_code": code,
                "duration_s": duration,
                "counts": counts,
                "error": error,
            }
        )
        self._log_outcome(running, outcome, code, counts, error, duration)
        if job.kind != "checks":
            if outcome in ("killed", "interrupted", "crashed"):
                runtime.repairs.append(
                    Repair(
                        unit,
                        job,
                        repair_days(unit, job, running.started_at, now),
                        should_retry(unit.trigger, outcome),
                    )
                )
            else:
                self._remove_inflight(unit.run_id)
            if unit.day:
                self._refill(unit, outcome == "success")
            if unit.trigger == "backfill":
                self._backfill_unit_ended(unit, outcome)
        else:
            self._remove_inflight(unit.run_id)
        self._tick_unit_ended(unit, outcome)
        if job.kind != "checks":
            self._source_outcome(runtime, outcome, result)

    def _refill(self: Any, unit: Unit, succeeded: bool) -> None:
        try:
            record_refill(self.store, unit.source, unit.job, unit.day, succeeded)
        except OSError as exc:
            self._state_failed(f"backfill/{unit.source}.{unit.job}.json", exc)

    # @spec SUPERVISOR-LOG-001, SUPERVISOR-LOG-003
    def _log_outcome(
        self: Any,
        running: Running,
        outcome: str,
        code: int,
        counts: Any,
        error: Any,
        duration: float,
    ) -> None:
        fields = {**run_fields(running.unit), "duration_s": duration, **(counts or {})}
        if outcome == "success":
            self.log.info("run_finished", **fields)
        elif outcome == "failed":
            self.log.warning("run_failed", **fields, error=error)
        elif outcome == "crashed":
            reason = "usage" if code == 2 else ("signal" if code < 0 else "exit")
            self.log.warning("run_crashed", **fields, exit_code=code, reason=reason)
        elif outcome == "killed":
            self.log.error(
                "run_killed", **fields, action=_KILLED_ACTION[running.job.kind == "checks"]
            )
        elif outcome == "busy":
            self.log.warning(
                "run_busy",
                **fields,
                reason="its lock was held, usually by a hand-started run or command",
            )
        elif outcome == "interrupted":
            self.log.info("run_interrupted", **fields)

    # @spec SUPERVISOR-AFTER-002
    def _run_repairs(self: Any) -> None:
        mono = self.clock.monotonic()
        for runtime in self.sources.values():
            if not runtime.repairs or mono < runtime.repair_retry_at:
                continue
            fd = try_lock(self.paths.state_path("locks", f"{runtime.name}.lock"))
            if fd is None:
                runtime.repair_retry_at = mono + pkg.REPAIR_LOCK_RETRY_SECONDS
                continue
            try:
                while runtime.repairs:
                    self._repair(runtime, runtime.repairs.pop(0))
            finally:
                release(fd)

    def _repair(self: Any, runtime: Any, repair: Repair) -> None:
        unit = repair.unit
        capture.repair(self.paths.data, unit.source, repair.days)
        self._append_run(
            {
                "type": "repaired",
                "origin": "daemon",
                **run_fields(unit),
                "day": unit.day,
                "days": repair.days,
            }
        )
        self.log.info("run_repaired", **run_fields(unit), days=repair.days)
        self._remove_inflight(unit.run_id)
        if repair.retry and runtime.state == "active" and not runtime.cooling(self.clock.now()):
            runtime.queue.append(Unit(unit.source, unit.job, unit.day, "retry"))
