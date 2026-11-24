"""`wearstream daemon`: validate, lock `/state`, start, and run the loop until stopped."""

from __future__ import annotations

import os
import signal
import threading

from wearstream import log
from wearstream.settings import apply_umask, ensure_valid, load, paths
from wearstream.state import release, try_lock
from wearstream.supervisor.core import StartupError, Supervisor

_STATE_ACTION = "Make /state writable by the container user, on a local filesystem."


# @spec SUPERVISOR-LIFE-001, SUPERVISOR-LIFE-002
def run_daemon() -> int:
    loaded = load(os.environ, "daemon")
    ensure_valid(loaded, "daemon")
    values = loaded.values
    apply_umask(values["WEARSTREAM_UMASK"])
    log.configure(fmt=values["WEARSTREAM_LOG_FORMAT"], level=values["WEARSTREAM_LOG_LEVEL"])
    log.quiet_libraries(garmin_debug=bool(values.get("WEARSTREAM_GARMIN_LIBRARY_DEBUG")))
    logger = log.get_logger("supervisor")
    try:
        lock = try_lock(paths(values).state_path("daemon.lock"))
    except OSError as exc:
        logger.error(
            "daemon_not_started",
            reason=f"/state is not writable: {exc.strerror}",
            action=_STATE_ACTION,
        )
        return 1
    if lock is None:
        logger.error(
            "daemon_not_started",
            reason="another daemon holds /state/daemon.lock",
            action="Another WearStream daemon is using this /state: "
            "stop it, or give each daemon its own /state.",
        )
        return 1
    try:
        return _loop(Supervisor(values), logger)
    finally:
        release(lock)


def _loop(supervisor: Supervisor, logger: object) -> int:
    try:
        supervisor.start()
    except StartupError as exc:
        logger.error("daemon_not_started", reason=str(exc), action=_STATE_ACTION)  # type: ignore[attr-defined]
        return 1
    wake, stop = threading.Event(), threading.Event()

    def on_signal(_signum: int, _frame: object) -> None:
        stop.set()
        wake.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    while True:
        if stop.is_set():
            supervisor.request_stop()
        supervisor.step()
        if supervisor.finished:
            return 0
        wake.wait(timeout=0.2 if supervisor.stopping else supervisor.sleep_seconds())
        wake.clear()
