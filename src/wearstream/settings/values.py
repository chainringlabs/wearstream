"""Turning one raw environment value into a typed, bounds-checked setting value."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from wearstream.settings.parse import (
    cron_minute,
    parse_bool,
    parse_cron,
    parse_date,
    parse_int,
    parse_list,
)
from wearstream.settings.registry import SOURCES, Setting

# Whoop's capture schedule may not refresh near the top of the hour, where grants were lost.
_WHOOP_BANNED_MINUTES = {*range(55, 60), *range(0, 6)}


def _bounded(setting: Setting, value: int) -> int:
    if setting.minimum is not None and value < setting.minimum:
        raise ValueError(f"must be at least {setting.minimum}")
    if setting.maximum is not None and value > setting.maximum:
        raise ValueError(f"must be at most {setting.maximum}")
    return value


# @spec OPERATOR-SET-002
def _cron(setting: Setting, raw: str) -> str:
    expression = parse_cron(raw)
    minute = cron_minute(expression)
    if minute is None or not 1 <= minute <= 59:
        raise ValueError("its minute must be one literal minute from 1 to 59")
    if setting.name == "WEARSTREAM_WHOOP_SCHEDULE" and minute in _WHOOP_BANNED_MINUTES:
        raise ValueError("its minute may not be from 55 to 59 or from 0 to 5")
    return expression


# @spec OPERATOR-SET-008
def _date(raw: str) -> dt.date:
    value = parse_date(raw)
    if value > dt.datetime.now(dt.UTC).date():
        raise ValueError("may not be in the future")
    return value


# @spec OPERATOR-SET-010
def _umask(raw: str) -> str:
    value = raw.strip()
    if not re.fullmatch(r"0?[0-7]{3}", value) or int(value, 8) > 0o077:
        raise ValueError("must be three or four octal digits from 000 to 077")
    return value


def _choice(setting: Setting, raw: str) -> str:
    for choice in setting.choices:
        if raw.strip().lower() == choice.lower():
            return choice
    raise ValueError("must be one of " + ", ".join(setting.choices))


# @spec OPERATOR-SET-001
def _sources(raw: str) -> list[str]:
    names = parse_list(raw)
    unknown = [name for name in names if name not in SOURCES]
    if unknown or not names:
        raise ValueError("must list whoop, garmin or both; unknown: " + ", ".join(unknown))
    return names


def convert(setting: Setting, raw: str) -> Any:
    """The typed value of `raw` for `setting`, or ValueError with the reason it is invalid."""
    kind = setting.kind
    if kind == "int":
        return _bounded(setting, parse_int(raw))
    if kind == "bool":
        return parse_bool(raw)
    if kind == "list":
        return [] if raw.strip().lower() == "none" else parse_list(raw)
    if kind == "cron":
        return _cron(setting, raw)
    if kind == "date":
        return _date(raw)
    if kind == "umask":
        return _umask(raw)
    if kind == "choice":
        return _choice(setting, raw)
    if kind == "sources":
        return _sources(raw)
    return raw.strip()
