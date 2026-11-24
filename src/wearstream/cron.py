"""Evaluating the five-field cron schedules configuration accepts, in UTC (supervisor design)."""

from __future__ import annotations

import datetime as dt
from functools import cached_property

UTC = dt.UTC
_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
)
_SEARCH_DAYS = 5 * 366 + 1  # long enough to reach the next 29 February
_HORIZON_START = dt.datetime(2024, 1, 1, tzinfo=UTC)


def _item(text: str, name: str, low: int, high: int) -> range:
    step, stepped = 1, "/" in text
    if stepped:
        text, step_text = text.split("/", 1)
        step = int(step_text)
        if step < 1:
            raise ValueError(f"the {name} step must be at least 1")
    if text == "*":
        start, end = low, high
    elif "-" in text:
        first, last = text.split("-", 1)
        start, end = int(first), int(last)
    else:
        start = int(text)
        end = high if stepped else start
    if not low <= start <= end <= high:
        raise ValueError(f"the {name} field must stay within {low}-{high}")
    return range(start, end + 1, step)


def _field(text: str, name: str, low: int, high: int) -> tuple[int, ...]:
    values: set[int] = set()
    for item in text.split(","):
        values.update(_item(item, name, low, high))
    return tuple(sorted(values))


class Cron:
    """A schedule: its next and latest fire times, and the gaps between fires."""

    def __init__(self, expression: str) -> None:
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError("a schedule has five fields")
        fields = [_field(text, *spec) for text, spec in zip(parts, _FIELDS, strict=True)]
        self.expression = expression
        self.minutes, self.hours, days, months, weekdays = fields
        self._days, self._months = set(days), set(months)
        self._weekdays = {day % 7 for day in weekdays}
        self._any_day, self._any_weekday = parts[2].startswith("*"), parts[4].startswith("*")
        try:
            self.next_after(_HORIZON_START)
        except LookupError:
            raise ValueError("the schedule never fires") from None

    def _day_matches(self, day: dt.date) -> bool:
        if day.month not in self._months:
            return False
        by_date, by_weekday = day.day in self._days, day.isoweekday() % 7 in self._weekdays
        if self._any_day and self._any_weekday:
            return True
        if self._any_day:
            return by_weekday
        if self._any_weekday:
            return by_date
        return by_date or by_weekday

    def _fires_on(self, day: dt.date) -> list[dt.datetime]:
        if not self._day_matches(day):
            return []
        return [
            dt.datetime(day.year, day.month, day.day, h, m, tzinfo=UTC)
            for h in self.hours
            for m in self.minutes
        ]

    def next_after(self, moment: dt.datetime) -> dt.datetime:
        """The first fire time strictly after `moment`."""
        moment = moment.astimezone(UTC)
        for offset in range(_SEARCH_DAYS):
            for fire in self._fires_on(moment.date() + dt.timedelta(days=offset)):
                if fire > moment:
                    return fire
        raise LookupError("no fire time within five years")

    def latest_at_or_before(self, moment: dt.datetime) -> dt.datetime:
        """The last fire time at or before `moment`."""
        moment = moment.astimezone(UTC)
        for offset in range(_SEARCH_DAYS):
            for fire in reversed(self._fires_on(moment.date() - dt.timedelta(days=offset))):
                if fire <= moment:
                    return fire
        raise LookupError("no fire time within five years")

    @cached_property
    def _gaps(self) -> tuple[dt.timedelta, dt.timedelta]:
        fires: list[dt.datetime] = []
        for offset in range(_SEARCH_DAYS):
            fires.extend(self._fires_on(_HORIZON_START.date() + dt.timedelta(days=offset)))
        gaps = [later - earlier for earlier, later in zip(fires, fires[1:], strict=False)]
        if not gaps:
            raise ValueError("the schedule fires too rarely to measure")
        return min(gaps), max(gaps)

    @property
    def interval(self) -> dt.timedelta:
        """The shortest gap between consecutive fire times."""
        return self._gaps[0]

    @property
    def longest_gap(self) -> dt.timedelta:
        """The longest gap between consecutive fire times."""
        return self._gaps[1]
