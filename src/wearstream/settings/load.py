"""Reading a process's configuration from its environment (operator-interface design).

`load` reads only the settings the command uses, checks every one, and collects every problem,
so the operator can fix them all in one pass. It never touches `/state` or the network.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from wearstream.settings.paths import path_problems
from wearstream.settings.registry import JOB_FIELDS, JOBS, REGISTRY, RETIRED, Setting
from wearstream.settings.values import convert

_WIDE_COMMANDS = ("daemon", "run", "config")  # commands that read every enabled source
_REFUSED_PREFIXES = ("WEARSTREAM_", "WHOOP_", "GARMIN")
_REFUSED_WORDS = ("SECRET", "PASSWORD", "PASSWD", "TOKEN", "API_KEY")
_JOB_SETTING = re.compile(r"WEARSTREAM_([A-Z]+)_([A-Z]+)_(" + "|".join(JOB_FIELDS) + ")")
_ACCEPT_SCHEMA = {"WEARSTREAM_GARMIN_SELECT", "WEARSTREAM_GARMIN_EXCLUDE"}


@dataclass
class Loaded:
    command: str
    source: str | None
    values: dict[str, Any] = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)


def _in_scope(setting: Setting, command: str, source: str | None, enabled: list[str]) -> bool:
    if setting.group in ("path", "logging"):
        return True
    if command == "auth":
        return setting.source == source
    if command == "accept-schema":
        is_checks_limit = setting.job == "checks" and setting.name.endswith("_TIME_LIMIT")
        return setting.source == source and (is_checks_limit or setting.name in _ACCEPT_SCHEMA)
    if command not in _WIDE_COMMANDS:
        return False
    return setting.source is None or setting.source in enabled


# @spec OPERATOR-ENV-001
def _refused(name: str, retired: frozenset[str]) -> bool:
    upper = name.upper()
    if name in REGISTRY or name in retired or upper == "GARMINTOKENS":
        return False
    return upper.startswith(_REFUSED_PREFIXES) and any(word in upper for word in _REFUSED_WORDS)


def _unknown(name: str) -> str:
    match = _JOB_SETTING.fullmatch(name)
    if match:
        source, job, setting_field = (part.lower() for part in match.groups())
        if job in JOBS.get(source, {}):
            return f"{name}: the {source} {job} job does not take a {setting_field} override"
        return f"{name}: {source} does not have a job named {job}"
    return f"{name} is not a WearStream setting (check the spelling)"


# @spec OPERATOR-SET-005, OPERATOR-SET-006
def _environment_problems(
    environ: Mapping[str, str], loaded: Loaded, retired: frozenset[str]
) -> None:
    for name in sorted(environ):
        if _refused(name, retired):
            loaded.problems.append(
                f"{name}: remove this variable; WearStream reads credentials only from files "
                "under /secrets"
            )
        elif name in retired:
            loaded.retired.append(name)
        elif name.startswith("WEARSTREAM_") and name not in REGISTRY:
            loaded.problems.append(_unknown(name))


def _enabled_sources(environ: Mapping[str, str], loaded: Loaded) -> list[str]:
    if loaded.command not in _WIDE_COMMANDS:
        return []
    raw = environ.get("WEARSTREAM_SOURCES", "")
    if not raw.strip():
        loaded.problems.append("WEARSTREAM_SOURCES is required: set it to whoop, garmin or both")
        return []
    try:
        enabled = convert(REGISTRY["WEARSTREAM_SOURCES"], raw)
    except ValueError as exc:
        loaded.problems.append(f"WEARSTREAM_SOURCES {exc}")
        return []
    loaded.values["WEARSTREAM_SOURCES"], loaded.origin["WEARSTREAM_SOURCES"] = (
        enabled,
        "environment",
    )
    return enabled


# @spec OPERATOR-PARSE-001, OPERATOR-SET-001, OPERATOR-SET-002, OPERATOR-SET-004, OPERATOR-SET-009
# @spec SUPERVISOR-JOB-002, SUPERVISOR-JOB-003, CHECKS-SET-001
def load(
    environ: Mapping[str, str] | None = None,
    command: str = "daemon",
    source: str | None = None,
    *,
    retired: frozenset[str] = RETIRED,
) -> Loaded:
    """Read, type and check the settings `command` uses; collect every problem found."""
    environ = os.environ if environ is None else environ
    loaded = Loaded(command, source)
    _environment_problems(environ, loaded, retired)
    enabled = _enabled_sources(environ, loaded)
    for setting in REGISTRY.values():
        if setting.kind == "sources":
            continue
        raw = environ.get(setting.name, "")
        if setting.source and command in _WIDE_COMMANDS and setting.source not in enabled:
            if raw.strip():
                loaded.ignored.append(setting.name)
            continue
        if not _in_scope(setting, command, source, enabled):
            continue
        if not raw.strip():
            loaded.values[setting.name], loaded.origin[setting.name] = setting.default, "default"
            continue
        try:
            loaded.values[setting.name] = convert(setting, raw)
            loaded.origin[setting.name] = "environment"
        except ValueError as exc:
            loaded.problems.append(f"{setting.name} {exc}")
    loaded.problems.extend(path_problems(loaded.values))
    if command in _WIDE_COMMANDS:
        from wearstream.checks.settings import source_setting_problems  # imports sources
        from wearstream.jobs import guardrail_problems  # jobs reads settings; import late

        loaded.problems.extend(guardrail_problems(loaded.values))
        loaded.problems.extend(source_setting_problems(loaded.values))
    return loaded
