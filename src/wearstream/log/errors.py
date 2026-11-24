"""What an exception contributes to a log event: its type, maybe its message, maybe frames."""

from __future__ import annotations

import traceback
from types import TracebackType
from typing import Any

from wearstream.log.setup import STATE

MESSAGE_LIMIT = 300
# Modules whose exception messages may carry request details or credentials.
TYPE_ONLY_MODULES = (
    "wearstream.whoop", "wearstream.garmin", "httpx", "httpcore", "curl_cffi", "garminconnect",
)  # fmt: skip


def _type_only_module(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in TYPE_ONLY_MODULES)


def _chain(exc: BaseException) -> list[BaseException]:
    """The exception and every cause or context behind it, oldest first."""
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen[::-1]


def _frames(tb: TracebackType | None) -> traceback.StackSummary:
    return traceback.StackSummary.extract(traceback.walk_tb(tb), lookup_lines=False)


def _from_type_only_module(exc: BaseException) -> bool:
    for item in _chain(exc):
        if _type_only_module(type(item).__module__):
            return True
        if any(
            _type_only_module(frame.f_globals.get("__name__", ""))
            for frame, _ in traceback.walk_tb(item.__traceback__)
        ):
            return True
    return False


def _traceback_text(exc: BaseException) -> str:
    """Frames and exception types for the whole chain; never a message, source line or local."""
    blocks = []
    for item in _chain(exc):
        lines = [type(item).__qualname__]
        lines += [f"  {f.filename}:{f.lineno} in {f.name}" for f in _frames(item.__traceback__)]
        blocks.append("\n".join(lines))
    return "\nwhich led to\n".join(blocks)


# @spec OPERATOR-LOG-006, OPERATOR-LOG-007, OPERATOR-LOG-010
def exception_fields(exc: BaseException) -> dict[str, Any]:
    """`error_type`; `error` (message, cut to 300 characters) unless the exception came from a
    source or an HTTP library; and at DEBUG a `traceback` of frames and types."""
    fields: dict[str, Any] = {"error_type": type(exc).__name__}
    if not _from_type_only_module(exc):
        fields["error"] = str(exc)[:MESSAGE_LIMIT]
    if STATE.debug:
        fields["traceback"] = _traceback_text(exc)
    return fields
