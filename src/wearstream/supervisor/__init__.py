"""The `wearstream daemon` supervisor: schedules, queues, child processes and `/state`."""

import sys

CHILD_PREFIX = [sys.executable, "-m", "wearstream"]  # how the daemon starts `wearstream run`
STOP_GRACE_SECONDS = 45  # SIGTERM to SIGKILL, for time limits and shutdown
REPAIR_LOCK_RETRY_SECONDS = 30

from wearstream.supervisor.core import StartupError, Supervisor  # noqa: E402
from wearstream.supervisor.daemon import run_daemon  # noqa: E402
from wearstream.supervisor.health import healthcheck  # noqa: E402
from wearstream.supervisor.spawn import ProcessSpawner, SystemClock  # noqa: E402
from wearstream.supervisor.units import Unit  # noqa: E402

__all__ = [
    "CHILD_PREFIX",
    "STOP_GRACE_SECONDS",
    "ProcessSpawner",
    "StartupError",
    "Supervisor",
    "SystemClock",
    "Unit",
    "healthcheck",
    "run_daemon",
]
