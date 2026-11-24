"""Fire times, ticks, catch-up, and counting failed ticks (supervisor design, *Schedules*)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from wearstream.state import iso
from wearstream.supervisor.runtime import JobSchedule, Tick
from wearstream.supervisor.units import Unit


class TickMixin:
    # @spec SUPERVISOR-SCHED-002
    def _init_schedules(self: Any, now: dt.datetime) -> None:
        for key, schedule in self.schedules.items():
            schedule.load(self.store.read(f"jobs/{key[0]}.{key[1]}.json") or {})
            start = max(now, schedule.last_tick) if schedule.last_tick else now
            schedule.next_fire = schedule.job.cron.next_after(start)

    def _save_job(self: Any, schedule: JobSchedule) -> None:
        self._write(f"jobs/{schedule.job.key}.json", schedule.doc())

    # @spec SUPERVISOR-SCHED-001, SUPERVISOR-SCHED-004
    def _fire_due(self: Any) -> None:
        now = self.clock.now()
        for schedule in self.schedules.values():
            if schedule.next_fire is None or now < schedule.next_fire:
                continue
            fire = schedule.job.cron.latest_at_or_before(now)
            schedule.next_fire = schedule.job.cron.next_after(now)
            if schedule.last_tick and fire <= schedule.last_tick:
                continue
            self._tick(schedule, fire, "schedule")

    def _has_scheduled_work(self: Any, schedule: JobSchedule) -> bool:
        runtime, name = self.sources[schedule.job.source], schedule.job.name
        units = [*runtime.queue, *runtime.checks_queue]
        units += [r.unit for r in (runtime.running, runtime.checks_running) if r]
        return any(u.job == name and u.trigger in ("schedule", "catchup") for u in units)

    # @spec SUPERVISOR-SCHED-005, SUPERVISOR-SCHED-006, SUPERVISOR-QUEUE-003, SUPERVISOR-QUEUE-004
    # @spec SUPERVISOR-RATE-002
    def _tick(self: Any, schedule: JobSchedule, at: dt.datetime, trigger: str) -> bool:
        job, runtime, now = schedule.job, self.sources[schedule.job.source], self.clock.now()
        if job.kind != "checks" and runtime.state == "waiting_for_auth":
            return False
        if job.kind != "checks" and runtime.cooling(now):
            self.log.info(
                "tick_skipped", source=job.source, job=job.name, reason="the source is rate-limited"
            )
            return False
        schedule.last_tick = max(at, schedule.last_tick) if schedule.last_tick else at
        if self._has_scheduled_work(schedule):
            self.log.info(
                "tick_skipped",
                source=job.source,
                job=job.name,
                reason="its previous tick is still queued or running",
            )
            self._save_job(schedule)
            return False
        tick_id = f"{job.key}@{iso(at)}#{trigger}"
        days = job.window(at.astimezone(dt.UTC).date()) if job.kind == "partitioned" else [None]
        units = [Unit(job.source, job.name, day, trigger, tick=tick_id) for day in days]
        self.ticks[tick_id] = Tick((job.source, job.name), at, trigger, {u.run_id for u in units})
        (runtime.checks_queue if job.kind == "checks" else runtime.queue).extend(units)
        self._save_job(schedule)
        return True

    # @spec SUPERVISOR-CATCHUP-001
    def _catch_up(self: Any, sources: list[str]) -> None:
        now = self.clock.now()
        for schedule in self.schedules.values():
            job = schedule.job
            if job.source not in sources:
                continue
            if schedule.last_completed and now - schedule.last_completed <= job.cron.interval:
                continue
            if self._tick(schedule, now, "catchup"):
                self.log.info("catchup_scheduled", source=job.source, job=job.name)

    def _drop_ticks(self: Any, units: list[Unit]) -> None:
        """Ticks whose units were dropped from a queue never complete."""
        for unit in units:
            if unit.tick:
                self.ticks.pop(unit.tick, None)

    def _tick_unit_ended(self: Any, unit: Unit, outcome: str) -> None:
        tick = self.ticks.get(unit.tick) if unit.tick else None
        if tick is None:
            return
        tick.pending.discard(unit.run_id)
        tick.outcomes.append(outcome)
        if not tick.pending:
            del self.ticks[unit.tick]
            self._complete_tick(tick)

    # @spec SUPERVISOR-FAIL-001, SUPERVISOR-FAIL-002, SUPERVISOR-FAIL-003, SUPERVISOR-BACKFILL-006
    def _complete_tick(self: Any, tick: Tick) -> None:
        schedule = self.schedules[tick.job]
        job = schedule.job
        schedule.last_completed = (
            max(tick.at, schedule.last_completed) if schedule.last_completed else tick.at
        )
        if job.kind != "checks":
            if any(o in ("failed", "killed", "crashed") for o in tick.outcomes):
                schedule.failed_ticks += 1
                if (
                    schedule.failed_ticks >= self.values["WEARSTREAM_FAILING_TICKS"]
                    and not schedule.failing
                ):
                    schedule.failing = True
                    self.log.error(
                        "job_failing",
                        source=job.source,
                        job=job.name,
                        failed_ticks=schedule.failed_ticks,
                        action="Read this job's recent run_failed lines: "
                        "they say which requests failed and why.",
                    )
            elif all(o == "success" for o in tick.outcomes):
                if schedule.failing:
                    self.log.info("job_recovered", source=job.source, job=job.name)
                schedule.failing, schedule.failed_ticks = False, 0
                today = self.clock.now().date().isoformat()
                if (
                    tick.trigger == "schedule"
                    and job.kind == "partitioned"
                    and schedule.backfill_day != today
                ):
                    schedule.backfill_day = today
                    self._start_pass(job)
        self._save_job(schedule)
