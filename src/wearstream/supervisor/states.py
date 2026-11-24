"""Credential waits, rate-limit cooldowns and the capture pause (supervisor design)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from wearstream import capture
from wearstream.sources import get_source

FINGERPRINT_EVERY = dt.timedelta(minutes=1)
REMIND_EVERY = dt.timedelta(hours=24)
DEFAULT_COOLDOWN, MAX_COOLDOWN = 3600, 86400
FIRST_DELAY, MAX_DELAY, RELAPSE_WINDOW = 300.0, 3600.0, dt.timedelta(hours=1)


def _pause_action(errno: str | None, folder: str | None) -> str:
    if errno in ("ENOSPC", "EDQUOT"):
        return f"Free space on the filesystem holding {folder}; capture resumes on its own."
    if errno == "EROFS":
        return f"{folder} is on a read-only filesystem: mount it read-write."
    if errno in ("EACCES", "EPERM"):
        return f"Make {folder} writable by the container user."
    return (
        f"Check {folder}: /data and /state must be writable by the container user, "
        "and /data must support hard links."
    )


class StatesMixin:
    def _save_source(self: Any, runtime: Any) -> None:
        self._write(f"sources/{runtime.name}.json", runtime.doc())

    def _fingerprint(self: Any, name: str) -> str | None:
        try:
            return get_source(name).fingerprint(self.paths)
        except Exception as exc:
            self.log.warning("fingerprint_failed", source=name, error_type=type(exc).__name__)
            return None

    def _drop_queue(self: Any, runtime: Any) -> None:
        self._drop_ticks(list(runtime.queue))
        runtime.queue.clear()
        for repair in runtime.repairs:
            repair.retry = False

    def _log_waiting(self: Any, runtime: Any) -> None:
        self.log.error(
            "source_waiting_for_auth",
            source=runtime.name,
            since=runtime.since.isoformat() if runtime.since else None,
            action=f"Run `wearstream auth {runtime.name}` (for example "
            f"`docker exec -it <container> wearstream auth {runtime.name}`).",
        )

    # @spec SUPERVISOR-AUTH-001
    def _enter_waiting(self: Any, runtime: Any, fingerprint: str | None) -> None:
        if runtime.state == "waiting_for_auth":
            return
        now = self.clock.now()
        runtime.fingerprint = fingerprint or self._fingerprint(runtime.name)
        runtime.state, runtime.since = "waiting_for_auth", now
        runtime.last_reminder = runtime.last_fingerprint_check = now
        self._drop_queue(runtime)
        self._save_source(runtime)
        self._log_waiting(runtime)

    # @spec SUPERVISOR-RATE-001, SUPERVISOR-RATE-003
    def _enter_cooldown(self: Any, runtime: Any, retry_after: object) -> None:
        now = self.clock.now()
        seconds = (
            retry_after
            if isinstance(retry_after, int)
            and not isinstance(retry_after, bool)
            and retry_after >= 0
            else DEFAULT_COOLDOWN
        )
        runtime.cooldown_until = now + dt.timedelta(seconds=min(seconds, MAX_COOLDOWN))
        runtime.rate_limits += 1
        self._drop_queue(runtime)
        self.log.warning(
            "source_rate_limited", source=runtime.name, until=runtime.cooldown_until.isoformat()
        )
        if runtime.rate_limits >= 3 and not runtime.repeatedly:
            runtime.repeatedly = True
            self.log.error(
                "source_rate_limited_repeatedly",
                source=runtime.name,
                times=runtime.rate_limits,
                action="Check the vendor's status page, "
                "and reduce this source's selection or raise its pacing.",
            )
        self._save_source(runtime)

    # @spec SUPERVISOR-STATE-007
    def _source_outcome(self: Any, runtime: Any, outcome: str, result: dict | None) -> None:
        if outcome == "success":
            runtime.last_success_at = self.clock.now()
            runtime.rate_limits, runtime.repeatedly = 0, False
            self._save_source(runtime)
        if outcome == "needs_auth" or (result or {}).get("needs_credential"):
            self._enter_waiting(runtime, (result or {}).get("fingerprint"))
        if outcome == "rate_limited":
            self._enter_cooldown(runtime, (result or {}).get("retry_after_seconds"))
        if outcome == "environment":
            env = (result or {}).get("environment") or {}
            folder = env.get("folder") or str(self.paths.data)
            self._enter_pause(
                f"a capture into {folder} failed with {env.get('errno') or 'an error'}",
                env.get("errno"),
                folder,
            )

    # @spec SUPERVISOR-AUTH-002, SUPERVISOR-AUTH-003, SUPERVISOR-RATE-004
    def _check_sources(self: Any) -> None:
        now = self.clock.now()
        for runtime in self.sources.values():
            if runtime.state == "waiting_for_auth":
                if now - (runtime.last_fingerprint_check or now) >= FINGERPRINT_EVERY:
                    runtime.last_fingerprint_check = now
                    fingerprint = self._fingerprint(runtime.name)
                    if fingerprint is not None and fingerprint != runtime.fingerprint:
                        runtime.state, runtime.fingerprint = "active", None
                        self.log.info("source_resumed", source=runtime.name)
                        self._resume_source(runtime)
                        continue
                if now - (runtime.last_reminder or now) >= REMIND_EVERY:
                    runtime.last_reminder = now
                    self._log_waiting(runtime)
                    self._save_source(runtime)
            if runtime.cooldown_until and now >= runtime.cooldown_until:
                runtime.cooldown_until = None
                self._resume_source(runtime)

    # @spec SUPERVISOR-AUTH-004
    def _resume_source(self: Any, runtime: Any) -> None:
        runtime.resumed_at = self.clock.now()
        self._save_source(runtime)
        self._catch_up([runtime.name])
        self._request_passes([runtime.name])

    # @spec SUPERVISOR-PAUSE-001, SUPERVISOR-PAUSE-004
    def _enter_pause(self: Any, reason: str, errno: str | None, folder: str | None) -> None:
        pause, now = self.pause, self.clock.now()
        if pause.active:
            return
        relapse = (
            pause.resumed_at is not None
            and now - pause.resumed_at < RELAPSE_WINDOW
            and pause.last_delay
        )
        pause.delay = min(pause.last_delay * 2, MAX_DELAY) if relapse else FIRST_DELAY
        pause.active, pause.reason, pause.errno, pause.folder = True, reason, errno, folder
        pause.next_retry = now + dt.timedelta(seconds=pause.delay)
        self.log.error(
            "capture_paused",
            reason=reason,
            errno=errno,
            folder=folder,
            action=_pause_action(errno, folder),
        )

    def _state_writable(self: Any) -> bool:
        try:
            self.store.write(".write-check.json", {"at": self.clock.now().isoformat()})
            self.store.path(".write-check.json").unlink()
            return True
        except OSError:
            return False

    def _failed_folder(self: Any) -> Path | None:
        folder, data = Path(self.pause.folder or self.paths.data), Path(self.paths.data)
        while folder != data and data in folder.parents and not folder.is_dir():
            folder = folder.parent
        return folder if folder != data and data in folder.parents else None

    # @spec SUPERVISOR-PAUSE-003, SUPERVISOR-PAUSE-005
    def _check_pause(self: Any) -> None:
        pause, now = self.pause, self.clock.now()
        if not pause.active or now < pause.next_retry:
            return
        folder = self._failed_folder()
        tested = (
            capture.self_test(self.paths.data, folder)
            if folder
            else capture.self_test(self.paths.data)
        )
        if not (tested.ok and self._state_writable()):
            pause.delay = min(pause.delay * 2, MAX_DELAY)
            pause.next_retry = now + dt.timedelta(seconds=pause.delay)
            return
        pause.active, pause.resumed_at, pause.last_delay = False, now, pause.delay
        self.log.info("capture_resumed")
        for runtime in self.sources.values():
            runtime.resumed_at = now
            self._save_source(runtime)
        self._catch_up(list(self.sources))
        self._request_passes(list(self.sources))
