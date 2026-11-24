"""What a check returns, and a source's own check."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

FAILING = ("warning", "error")


@dataclass
class Result:
    """`severity` is `pass`, `warning` or `error`; `status` names the finding. `suppressed` holds
    the reason when capture can't happen; `baseline` is a write the unit makes once accepted."""

    severity: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)
    action: str | None = None
    suppressed: str | None = None
    baseline: dict[str, Any] | None = None

    @property
    def failing(self) -> bool:
        return self.severity in FAILING


# @spec CHECKS-RUN-007
@dataclass(frozen=True)
class OwnCheck:
    """A source's own check: `run` takes the suppression reason (or None) and returns a Result."""

    name: str
    run: Callable[[str | None], Result]
