"""The contract between the supervisor and a source, and the registry of sources.

A source provides, without network calls unless it is running a unit:

- `collections(job, values)`: the collections a `partitioned` job writes (backfill's "has data");
- `fingerprint(paths)`: a fingerprint of its credential, computed without a network call;
- `check_settings(values)`: its collection check settings (`wearstream.checks.CheckSetting`),
  from the effective configuration;
- `own_checks(values, paths)`: its own checks (`wearstream.checks.OwnCheck`), run first;
- `run(ctx)`: run one unit through a `wearstream.runner.RunContext`, capturing with
  `ctx.capture`, reporting with `ctx.failed_request`, `ctx.needs_credential` and
  `ctx.rate_limited`, and wrapping every credential exchange in `ctx.credential_exchange()`;
  its `checks` job's unit calls `wearstream.checks.run_unit` with its settings and own checks.

A source module registers itself in `REGISTRY` when imported (`wearstream.<name>`).
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol


class Source(Protocol):
    def collections(self, job: str, values: dict[str, Any]) -> tuple[str, ...]: ...

    def fingerprint(self, paths: Any) -> str: ...

    def check_settings(self, values: dict[str, Any]) -> list[Any]: ...

    def own_checks(self, values: dict[str, Any], paths: Any) -> list[Any]: ...

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
