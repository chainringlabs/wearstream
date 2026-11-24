"""A scripted source for tests that run real `wearstream` processes.

The script is a JSON file named by `FAKE_SOURCE_SCRIPT`: `{"<source>.<job>": [action, ...],
"fingerprint": "..."}`. Each action is a one-key object; see `_act`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from wearstream import sources


def _script() -> dict[str, Any]:
    path = os.environ.get("FAKE_SOURCE_SCRIPT")
    return json.loads(Path(path).read_text()) if path else {}


class ScriptedSource:
    def __init__(self, name: str) -> None:
        self.name = name

    def collections(self, job: str, values: dict[str, Any]) -> tuple[str, ...]:
        return ("sleep",)

    def fingerprint(self, paths: Any) -> str:
        return _script().get("fingerprint", "fp-0")

    def run(self, ctx: Any) -> None:
        for action in _script().get(ctx.job.key, []):
            [(kind, arg)] = action.items()
            _act(ctx, kind, arg)


def _act(ctx: Any, kind: str, arg: Any) -> None:
    if kind == "capture":
        meta = {"http_status": arg.get("status", 200), "content_type": "application/json"}
        payload = arg.get("payload", "{}").encode()
        ctx.capture(arg.get("collection", "sleep"), payload, meta, dt=arg.get("dt"))
    elif kind == "sleep":
        deadline = time.monotonic() + arg
        while time.monotonic() < deadline:
            time.sleep(0.05)
    elif kind == "exchange":
        with ctx.credential_exchange():
            Path(arg["marker"]).write_text("in exchange")
            time.sleep(arg["seconds"])
            Path(arg["marker"]).write_text("exchange done")
    elif kind == "touch":
        Path(arg).write_text("reached")
    elif kind == "needs_auth":
        ctx.needs_credential(arg)
    elif kind == "rate_limited":
        ctx.rate_limited(arg)
    elif kind == "fail_request":
        ctx.failed_request(arg)
    elif kind == "raise":
        raise RuntimeError(arg)


def install() -> None:
    for name in ("whoop", "garmin"):
        sources.REGISTRY[name] = ScriptedSource(name)
