"""The contract between the supervisor and a source, and the registry of sources.

A source provides, without network calls unless it is running a unit:

- `collections(job, values)`: the collections a `partitioned` job writes (backfill's "has data");
- `fingerprint(paths)`: a fingerprint of its credential, computed without a network call;
- `run(ctx)`: run one unit through a `wearstream.runner.RunContext`, capturing with
  `ctx.capture`, reporting with `ctx.failed_request`, `ctx.needs_credential` and
  `ctx.rate_limited`, and wrapping every credential exchange in `ctx.credential_exchange()`.

A source module registers itself in `REGISTRY` when imported (`wearstream.<name>`).
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol


class Source(Protocol):
    def collections(self, job: str, values: dict[str, Any]) -> tuple[str, ...]: ...

    def fingerprint(self, paths: Any) -> str: ...

    def run(self, ctx: Any) -> None: ...


REGISTRY: dict[str, Source] = {}


def get_source(name: str) -> Source:
    """The registered source, importing `wearstream.<name>` the first time it is asked for."""
    if name not in REGISTRY:
        try:
            importlib.import_module(f"wearstream.{name}")
        except ModuleNotFoundError:
            pass
    if name not in REGISTRY:
        raise LookupError(f"the {name} source is not available in this build")
    return REGISTRY[name]
