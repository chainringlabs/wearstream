"""Backfill passes: finding missing days off the loop and feeding them to an empty queue."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wearstream import capture
from wearstream.sources import get_source
from wearstream.state.refill import backfill_doc
from wearstream.supervisor.units import Unit

RECHECK_AFTER_DAYS, MAX_ATTEMPTS = 30, 3
ZERO = {"missing": 0, "filled": 0, "known_empty": 0, "given_up": 0}


@dataclass
class _Feed:
    future: Any = None
    last_end: dt.datetime | None = None


def _has_data(data: Path, source: str, collections: tuple[str, ...], day: str) -> bool:
    return any(capture.has_data(data, source, c, day) for c in collections)


def _earliest_with_data(data: Path, source: str, collections: tuple[str, ...]) -> dt.date | None:
    found = []
    for collection in collections:
        try:
            days = sorted(
                p.name[3:]
                for p in (data / source / collection).iterdir()
                if p.name.startswith("dt=")
            )
        except OSError:
            continue
        found += [
            dt.date.fromisoformat(d)
            for d in days[:1000]
            if capture.has_data(data, source, collection, d)
        ][:1]
    return min(found, default=None)


# @spec SUPERVISOR-BACKFILL-001, SUPERVISOR-BACKFILL-002, SUPERVISOR-BACKFILL-003
# @spec SUPERVISOR-BACKFILL-004, SUPERVISOR-BACKFILL-010, SUPERVISOR-BACKFILL-012
def next_missing(
    data: Path,
    source: str,
    collections: tuple[str, ...],
    top: dt.date,
    start_from: dt.date | None,
    first_run: dt.date,
    today: dt.date,
    doc: dict[str, Any],
) -> str | None:
    """The newest missing day from `top` back to the backfill start, or None. Runs off the loop."""
    start = start_from or _earliest_with_data(data, source, collections) or first_run
    refill, known_empty, attempts = (
        set(doc.get("needs_refill", [])),
        doc.get("known_empty", {}),
        doc.get("attempts", {}),
    )
    day = top
    while day >= start:
        text = day.isoformat()
        if attempts.get(text, 0) < MAX_ATTEMPTS:
            if text in refill:
                return text
            empty = known_empty.get(text)
            if empty:
                recorded = dt.date.fromisoformat(empty.get("recorded", today.isoformat()))
                if not empty.get("rechecked") and (today - recorded).days >= RECHECK_AFTER_DAYS:
                    return text
            elif not _has_data(data, source, collections, text):
                return text
        day -= dt.timedelta(days=1)
    return None


class BackfillMixin:
    def _partitioned(self: Any, sources: list[str]) -> list[Any]:
        return [
            job for job in self.jobs.values() if job.kind == "partitioned" and job.source in sources
        ]

    def _collections(self: Any, job: Any) -> tuple[str, ...]:
        try:
            return tuple(get_source(job.source).collections(job.name, self.values))
        except LookupError:
            return ()

    # @spec SUPERVISOR-BACKFILL-006, SUPERVISOR-BACKFILL-008
    def _startup_backfill(self: Any) -> None:
        for job in self._partitioned(list(self.sources)):
            if not (self.store.read(backfill_doc(job.source, job.name)) or {}).get("in_progress"):
                self._start_pass(job)

    def _request_passes(self: Any, sources: list[str]) -> None:
        for job in self._partitioned(sources):
            self._start_pass(job)

    # @spec SUPERVISOR-BACKFILL-007
    def _start_pass(self: Any, job: Any) -> None:
        started = []

        def change(doc: dict | None) -> dict:
            doc = dict(doc or {})
            if doc.get("in_progress"):
                doc["restart_pending"] = True
            else:
                doc.update(
                    in_progress=True, position=None, restart_pending=False, counts=dict(ZERO)
                )
                started.append(True)
            return doc

        self._update(backfill_doc(job.source, job.name), change)
        if started:
            self.log.info("backfill_pass_started", source=job.source, job=job.name)

    def _finish_pass(self: Any, job: Any) -> None:
        state: dict[str, Any] = {}

        def change(doc: dict | None) -> dict:
            doc = dict(doc or {})
            state.update(
                restart=doc.get("restart_pending", False), counts=doc.get("counts") or dict(ZERO)
            )
            doc.update(in_progress=False, position=None, restart_pending=False)
            return doc

        self._update(backfill_doc(job.source, job.name), change)
        self.log.info(
            "backfill_pass_finished", source=job.source, job=job.name, **state.get("counts", ZERO)
        )
        if state.get("restart"):
            self._start_pass(job)

    def _backfill_ready(self: Any, job: Any, feed: _Feed) -> bool:
        runtime, now = self.sources[job.source], self.clock.now()
        if (
            self.pause.active
            or runtime.state != "active"
            or runtime.cooling(now)
            or runtime.repairs
        ):
            return False
        if runtime.queue or runtime.running:
            return False
        return not (feed.last_end and (now - feed.last_end).total_seconds() < job.backfill_pause)

    # @spec SUPERVISOR-BACKFILL-005, SUPERVISOR-QUEUE-005
    def _feed_backfill(self: Any) -> None:
        if self.stopping:
            return
        for job in self._partitioned(list(self.sources)):
            feed = self._backfill.setdefault((job.source, job.name), _Feed())
            if not self._backfill_ready(job, feed):
                continue
            if feed.future is None and not self._submit_scan(job, feed):
                continue
            if not feed.future.done():
                continue
            future, feed.future = feed.future, None
            try:
                day = future.result()
            except Exception as exc:
                self.log.warning(
                    "backfill_scan_failed",
                    source=job.source,
                    job=job.name,
                    error_type=type(exc).__name__,
                )
                day = None
            if day is None:
                self._finish_pass(job)
                continue
            self.sources[job.source].queue.append(Unit(job.source, job.name, day, "backfill"))
            self._update(
                backfill_doc(job.source, job.name),
                lambda d: {
                    **(d or {}),
                    "counts": {
                        **ZERO,
                        **(d or {}).get("counts", {}),
                        "missing": (d or {}).get("counts", {}).get("missing", 0) + 1,
                    },
                },
            )

    def _submit_scan(self: Any, job: Any, feed: _Feed) -> bool:
        doc = self.store.read_quiet(backfill_doc(job.source, job.name)) or {}
        if not doc.get("in_progress"):
            return False
        collections = self._collections(job)
        if not collections:
            self._finish_pass(job)
            return False
        today = self.clock.now().date()
        end = today if job.include_today else today - dt.timedelta(days=1)
        top = end - dt.timedelta(days=job.window_days)
        if doc.get("position"):
            top = min(top, dt.date.fromisoformat(doc["position"]) - dt.timedelta(days=1))
        runtime = self.sources[job.source]
        first_run = dt.date.fromisoformat(runtime.first_run) if runtime.first_run else today
        start_from = self.values.get(f"WEARSTREAM_{job.source.upper()}_BACKFILL_FROM")
        feed.future = self.executor.submit(
            next_missing,
            self.paths.data,
            job.source,
            collections,
            top,
            start_from,
            first_run,
            today,
            doc,
        )
        return True

    # @spec SUPERVISOR-BACKFILL-009, SUPERVISOR-BACKFILL-011, SUPERVISOR-BACKFILL-013
    def _backfill_unit_ended(self: Any, unit: Unit, outcome: str) -> None:
        job = self.jobs[unit.source, unit.job]
        self._backfill.setdefault((job.source, job.name), _Feed()).last_end = self.clock.now()
        has = outcome == "success" and _has_data(
            self.paths.data, job.source, self._collections(job), unit.day
        )
        today, abandoned = self.clock.now().date().isoformat(), []

        def change(doc: dict | None) -> dict:
            doc = dict(doc or {})
            counts, empty, attempts = (
                {**ZERO, **doc.get("counts", {})},
                dict(doc.get("known_empty", {})),
                dict(doc.get("attempts", {})),
            )
            if outcome == "success" and has:
                counts["filled"] += 1
                empty.pop(unit.day, None)
            elif outcome == "success":
                if unit.day in empty:
                    empty[unit.day] = {**empty[unit.day], "rechecked": True}
                else:
                    empty[unit.day] = {"recorded": today, "rechecked": False}
                    counts["known_empty"] += 1
            elif outcome not in ("interrupted", "busy", "rate_limited"):
                attempts[unit.day] = attempts.get(unit.day, 0) + 1
                if attempts[unit.day] == MAX_ATTEMPTS:
                    counts["given_up"] += 1
                    abandoned.append(unit.day)
            doc.update(position=unit.day, counts=counts, known_empty=empty, attempts=attempts)
            return doc

        self._update(backfill_doc(job.source, job.name), change)
        for day in abandoned:
            self.log.warning(
                "backfill_day_abandoned",
                source=job.source,
                job=job.name,
                day=day,
                attempts=MAX_ATTEMPTS,
            )
