"""`wearstream` with the scripted fake source installed, for tests that run real processes.

`FAKE_SECONDS_PER_MINUTE` shrinks time limits and `FAKE_GRACE_SECONDS` the stop grace, so
limits can be exercised in seconds. Children the daemon starts run through this file too.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_source  # noqa: E402

from wearstream import jobs, runner, supervisor  # noqa: E402
from wearstream.cli import entry  # noqa: E402

fake_source.install()
if os.environ.get("FAKE_SECONDS_PER_MINUTE"):
    jobs.SECONDS_PER_MINUTE = int(os.environ["FAKE_SECONDS_PER_MINUTE"])
if os.environ.get("FAKE_GRACE_SECONDS"):
    runner.STOP_GRACE_SECONDS = supervisor.STOP_GRACE_SECONDS = int(
        os.environ["FAKE_GRACE_SECONDS"]
    )
supervisor.CHILD_PREFIX = [sys.executable, os.path.abspath(__file__)]
entry()
