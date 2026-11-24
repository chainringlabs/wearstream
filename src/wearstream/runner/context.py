"""What a source runs inside: capturing into the right day, reporting, and stop signals."""

from __future__ import annotations

import signal
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from wearstream import capture as capture_component
from wearstream.capture import CaptureResult
from wearstream.runner import ENVIRONMENT, FAILED, NEEDS_AUTH, OK, RATE_LIMITED, STOPPED


class Stopped(BaseException):
    """A stop signal (or a hand-started run's own time limit) ended the run."""


class EndUnit(BaseException):
    """A capture failed in a way every later capture would repeat; the job ends here."""


class Signals:
    """Turns `SIGTERM` and `SIGINT` into `Stopped`, held while a credential exchange runs."""

    def __init__(self) -> None:
        self.received = False
        self.raising = True
        self._depth = 0
        self._pending = False

    def install(self) -> None:
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, _signum: int, _frame: object) -> None:
        self.received = True
        if self._depth:
            self._pending = True
        elif self.raising:
            raise Stopped

    # @spec SUPERVISOR-CHILD-008
    @contextmanager
    def exchange(self) -> Iterator[None]:
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if not self._depth and self._pending:
                self._pending = False
                if self.raising:
                    raise Stopped


class RunContext:
    """One unit's view of WearStream: `capture`, the reports a source makes, and the exchange."""

    def __init__(
        self,
        job: Any,
        day: str | None,
        trigger: str,
        run_id: str,
        values: dict[str, Any],
        paths: Any,
        log: Any,
        signals: Signals,
    ) -> None:
        self.job, self.source, self.day = job, job.source, day
        self.trigger, self.run_id = trigger, run_id
        self.values, self.paths, self.log, self.signals = values, paths, log, signals
        self.counts = {"written": 0, "skipped_identical": 0, "skipped_non_2xx": 0, "failed": 0}
        self.errors: list[str] = []
        self.environment: dict[str, str | None] | None = None
        self.request_failed = self.credential_needed = self.limited = self.stopped = False
        self.fingerprint: str | None = None
        self.retry_after: int | None = None

    # @spec SUPERVISOR-CHILD-004, SUPERVISOR-CHILD-009
    def capture(
        self, collection: str, payload: bytes, meta: dict[str, Any], **options: Any
    ) -> CaptureResult:
        """Capture through the capture component; a partitioned unit always writes its own day."""
        if self.job.kind == "partitioned":
            options["dt"] = self.day
        result = capture_component.capture(
            self.paths.data, self.source, collection, payload, meta, **options
        )
        self.counts[result.outcome.value] += 1
        if result.error_kind == "environment":
            self.environment = {"errno": result.errno, "folder": result.folder}
            self.errors.append(f"{collection}: {result.errno or 'write failed'}")
            raise EndUnit
        return result

    def failed_request(self, summary: str) -> None:
        self.request_failed = True
        self.errors.append(summary)

    def needs_credential(self, fingerprint: str | None) -> None:
        self.credential_needed = True
        self.fingerprint = fingerprint

    def rate_limited(self, retry_after_seconds: float | None = None) -> None:
        self.limited = True
        if retry_after_seconds is not None:
            self.retry_after = int(retry_after_seconds)

    def credential_exchange(self) -> Any:
        return self.signals.exchange()

    # @spec SUPERVISOR-CHILD-005
    def exit_code(self) -> int:
        if self.stopped or self.signals.received:
            return STOPPED
        if self.environment:
            return ENVIRONMENT
        if self.credential_needed:
            return NEEDS_AUTH
        if self.limited:
            return RATE_LIMITED
        if self.request_failed or self.counts["failed"]:
            return FAILED
        return OK

    # @spec SUPERVISOR-CHILD-003
    def result(self) -> dict[str, Any]:
        return {
            **self.counts,
            "error": "; ".join(self.errors)[:300] or None,
            "environment": self.environment,
            "needs_credential": self.credential_needed,
            "fingerprint": self.fingerprint,
            "rate_limited": self.limited,
            "retry_after_seconds": self.retry_after,
        }
