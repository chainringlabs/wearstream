"""Parsing rules for setting values (operator-interface design, *Parsing rules*).

Each parser takes the raw string from the environment and returns a typed value or raises
`ValueError` with a short reason that becomes part of a configuration problem.
"""

from __future__ import annotations

import datetime as dt
import re

_CRON_FIELD = re.compile(r"[0-9*,/\-]+")


# @spec OPERATOR-PARSE-002
def parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("must be true or false")


# @spec OPERATOR-PARSE-003
def parse_list(raw: str) -> list[str]:
    items: list[str] = []
    for part in raw.split(","):
        item = part.strip().lower()
        if item and item not in items:
            items.append(item)
    return items


# @spec OPERATOR-PARSE-004
def parse_int(raw: str) -> int:
    value = raw.strip()
    if not re.fullmatch(r"\d+", value):
        raise ValueError("must be a whole number")
    return int(value)


# @spec OPERATOR-PARSE-004
def parse_date(raw: str) -> dt.date:
    value = raw.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("must be a date written YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError("is not a real calendar date") from None


# @spec OPERATOR-PARSE-004
def parse_cron(raw: str) -> str:
    fields = raw.split()
    if len(fields) != 5 or not all(_CRON_FIELD.fullmatch(field) for field in fields):
        raise ValueError("must be a five-field cron expression, such as '17 * * * *'")
    return " ".join(fields)


def cron_minute(expression: str) -> int | None:
    """The schedule's minute when its minute field is one literal number, else None."""
    first = expression.split()[0]
    return int(first) if first.isdecimal() else None
