"""The text `wearstream config` prints: effective settings, secret files, and problems."""

from __future__ import annotations

from typing import Any

from wearstream.settings.load import Loaded
from wearstream.settings.paths import Paths

SECRET_FILES = (
    ("whoop", "client_id"),
    ("whoop", "client_secret"),
    ("whoop", "token.json"),
    ("garmin", "email"),
    ("garmin", "password"),
    ("garmin", "garmin_tokens.json"),
)


def _show(value: Any) -> str:
    if value is None:
        return "(unset)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(value) if value else "(empty)"
    return str(value)


# @spec OPERATOR-CONFIG-001
def describe(loaded: Loaded, paths: Paths | None) -> str:
    """Every setting with its value and origin, which secret files exist, and every problem."""
    lines = ["Settings:"]
    for name in sorted(loaded.values):
        lines.append(f"  {name} = {_show(loaded.values[name])} ({loaded.origin[name]})")
    if paths is not None:
        lines.append(f"Secret files (under {paths.secrets}; contents never shown):")
        for source, name in SECRET_FILES:
            state = "present" if paths.secrets_path(source, name).is_file() else "missing"
            lines.append(f"  {source}/{name}: {state}")
    for title, names in (("Ignored", loaded.ignored), ("Retired", loaded.retired)):
        if names:
            lines.append(f"{title} settings: " + ", ".join(names))
    if loaded.problems:
        lines.append("Problems:")
        lines.extend(f"  - {problem}" for problem in loaded.problems)
    else:
        lines.append("The configuration is valid.")
    return "\n".join(lines)
