"""The supervisor: one object that owns time, queues and `/state` for the daemon."""

from __future__ import annotations

import errno as errno_names
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import wearstream.supervisor as pkg
from wearstream import __version__, capture, log
from wearstream.jobs import effective_jobs
from wearstream.settings import paths
from wearstream.state import StateStore, iso, parse_time, pid_namespace
from wearstream.supervisor.backfill import BackfillMixin
from wearstream.supervisor.recovery import RecoveryMixin
from wearstream.supervisor.running import RunningMixin
from wearstream.supervisor.runtime import JobSchedule, PauseState, SourceRuntime, Tick
from wearstream.supervisor.spawn import ProcessSpawner, SystemClock
from wearstream.supervisor.states import StatesMixin
from wearstream.supervisor.ticks import TickMixin
from wearstream.supervisor.units import Unit

TERM, KILL = 15, 9


class StartupError(Exception):
    """The daemon cannot start: `/state` is not writable."""


# @spec SUPERVISOR-LOG-002
class Supervisor(RunningMixin, TickMixin, StatesMixin, BackfillMixin, RecoveryMixin):
    """Drive it with `start()`, then `step()` in a loop; `request_stop()` begins shutdown."""

    def __init__(
        self, values: dict[str, Any], *, clock: Any = None, spawn: Any = None, executor: Any = None
    ) -> None:
        self.values, self.paths = values, paths(values)
        self.clock = clock or SystemClock()
        self._spawn = spawn or ProcessSpawner()
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="backfill")
        self.jobs = effective_jobs(values)
        self.store = StateStore(self.paths.state)
        self.log = log.get_logger("supervisor")
        self.sources = {name: SourceRuntime(name) for name in values["WEARSTREAM_SOURCES"]}
        self.schedules = {key: JobSchedule(job) for key, job in self.jobs.items()}
        self.ticks: dict[str, Tick] = {}
        self.pause = PauseState()
        self.phase, self.stopping, self.finished = "starting", False, False
        self.previous_stop = None
        self._recovered = False
        self._stop_deadline = 0.0
        self._minute_mark = 0.0
        self._reported_manual: set[str] = set()
        self._backfill: dict[tuple[str, str], Any] = {}

    # @spec SUPERVISOR-LIFE-002, OPERATOR-LOG-005, OPERATOR-LOG-009
    def start(self) -> None:
        previous = self.store.read_quiet("heartbeat.json")
        self.store.established = previous is not None
        self.previous_stop = parse_time((previous or {}).get("updated_at"))
        try:
            self.store.write("heartbeat.json", self._heartbeat_doc())
        except OSError as exc:
            raise StartupError(f"/state is not writable: {exc}") from exc
        now = self.clock.now()
        for runtime in self.sources.values():
            runtime.load(self.store.read(f"sources/{runtime.name}.json") or {})
            runtime.first_run = runtime.first_run or now.date().isoformat()
            self._save_source(runtime)
        self._init_schedules(now)
        tested = capture.self_test(self.paths.data)
        if not tested.ok:
            self._enter_pause(
                f"/data failed its self-test at the {tested.step} step: {tested.error}",
                None,
                str(self.paths.data),
            )
        self.phase = "running"
        self._minute_mark = self.clock.monotonic()
        self._reported_manual = self._manual_finishes()
        self._heartbeat()
        schedules = {job.key: job.schedule for job in self.jobs.values()}
        self.log.info(
            "daemon_started", version=__version__, sources=list(self.sources), schedules=schedules
        )
        log.startup_warnings(self.values)

    def step(self) -> None:
        """One pass of the scheduling loop."""
        if self.stopping:
            self._step_stopping()
            return
        if not self._recovered:
            self._recover()
            self._run_repairs()
            self._catch_up(list(self.sources))
            self._startup_backfill()
            self._recovered = True
        self._poll_units()
        self._run_repairs()
        self._check_sources()
        self._check_pause()
        self._minute_tasks()
        self._fire_due()
        self._feed_backfill()
        self._start_units()
        self._heartbeat()

    # @spec SUPERVISOR-LIFE-004
    def request_stop(self) -> None:
        if self.stopping:
            return
        self.stopping, self.phase = True, "stopping"
        self._heartbeat()
        self.log.info("daemon_stopping")
        for running in self._all_running():
            running.signalled = running.signalled or "shutdown"
            running.handle.signal(TERM)
        own = pid_namespace()
        for entry in (self.store.read_quiet("inflight.json") or {}).get("units", []):
            if (
                entry.get("origin") == "manual"
                and entry.get("pidns") == own
                and isinstance(entry.get("pid"), int)
            ):
                try:
                    os.kill(entry["pid"], TERM)
                except ProcessLookupError, PermissionError:
                    pass
        self._stop_deadline = self.clock.monotonic() + pkg.STOP_GRACE_SECONDS

    def _step_stopping(self) -> None:
        live = [r for r in self._all_running() if r.handle.poll() is None]
        if not live:
            self.finished = True
        elif self.clock.monotonic() > self._stop_deadline:
            for running in live:
                running.handle.signal(KILL)
            self.finished = True
        self._heartbeat()

    def _all_running(self) -> list[Any]:
        return [r for rt in self.sources.values() for r in (rt.running, rt.checks_running) if r]

    # Inspection, used by the daemon loop and by tests.
    def queued(self, source: str) -> list[Unit]:
        return list(self.sources[source].queue)

    def queued_checks(self, source: str) -> list[Unit]:
        return list(self.sources[source].checks_queue)

    def running(self, source: str) -> Unit | None:
        running = self.sources[source].running
        return running.unit if running else None

    def running_checks(self, source: str) -> Unit | None:
        running = self.sources[source].checks_running
        return running.unit if running else None

    def idle(self) -> bool:
        return (
            not any(rt.queue or rt.checks_queue or rt.repairs for rt in self.sources.values())
            and not self._all_running()
        )

    # @spec SUPERVISOR-SCHED-003
    def sleep_seconds(self) -> float:
        if self._all_running() or any(rt.repairs for rt in self.sources.values()):
            return 1.0
        now = self.clock.now()
        fires = [s.next_fire for s in self.schedules.values() if s.next_fire]
        nearest = min((f - now).total_seconds() for f in fires) if fires else 30.0
        return max(0.0, min(30.0, nearest))

    # @spec SUPERVISOR-HEALTH-001
    def _heartbeat_doc(self) -> dict[str, Any]:
        now = self.clock.now()
        return {
            "pid": os.getpid(),
            "version": __version__,
            "phase": self.phase,
            "updated_at": iso(now),
            "pause": self.pause.doc(),
            "sources": {name: rt.status(now) for name, rt in self.sources.items()},
        }

    def _heartbeat(self) -> None:
        self._write("heartbeat.json", self._heartbeat_doc())

    # Every `/state` write goes through these: a failed write pauses capture (SUPERVISOR-PAUSE-001).
    def _write(self, relative: str, doc: dict[str, Any]) -> bool:
        try:
            self.store.write(relative, doc)
            return True
        except OSError as exc:
            self._state_failed(relative, exc)
            return False

    def _update(self, relative: str, change: Any) -> dict[str, Any] | None:
        try:
            return self.store.update(relative, change)
        except OSError as exc:
            self._state_failed(relative, exc)
            return None

    def _append_run(self, line: dict[str, Any]) -> None:
        try:
            self.store.append_run(line, self.clock.now())
        except OSError as exc:
            self._state_failed("runs", exc)

    def _state_failed(self, relative: str, exc: OSError) -> None:
        name = errno_names.errorcode.get(exc.errno or 0)
        self._enter_pause(
            f"could not write {self.store.path(relative)}: {exc.strerror or exc}",
            name,
            str(self.store.path(relative)),
        )
