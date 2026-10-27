"""Small dependency-free five-field cron parser used by automations.

Supported syntax is the conventional ``minute hour day-of-month month weekday``
form with ``*``, comma lists, inclusive ranges and ``/step``. Weekdays accept 0 or
7 for Sunday. Matching follows Vixie cron's day rule: when both day-of-month and
weekday are restricted, either may match. Time zones use the stdlib ``zoneinfo``
database; ``local`` uses the machine's local zone.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class CronError(ValueError):
    pass


@dataclass(frozen=True)
class Cron:
    minute: frozenset[int]
    hour: frozenset[int]
    day: frozenset[int]
    month: frozenset[int]
    weekday: frozenset[int]
    day_any: bool
    weekday_any: bool

    def matches(self, date: datetime) -> bool:
        # Python Monday=0; cron Sunday=0, Monday=1.
        weekday = (date.weekday() + 1) % 7
        day_match, weekday_match = date.day in self.day, weekday in self.weekday
        days = day_match and weekday_match if self.day_any or self.weekday_any else day_match or weekday_match
        return date.minute in self.minute and date.hour in self.hour and date.month in self.month and days


FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day of month", 1, 31), ("month", 1, 12), ("weekday", 0, 7))


def _field(text: str, label: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    if not text:
        raise CronError(f"Cron {label} is empty")
    for part in text.split(","):
        base, slash, step_text = part.partition("/")
        if slash:
            if not step_text.isdigit() or not 1 <= int(step_text) <= high - low + 1:
                raise CronError(f"Cron {label} has an invalid step")
            step = int(step_text)
        else:
            step = 1
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, sep, last = base.partition("-")
            if not sep or not first.isdigit() or not last.isdigit():
                raise CronError(f"Cron {label} has an invalid range")
            start, end = int(first), int(last)
        elif base.isdigit():
            start = end = int(base)
        else:
            raise CronError(f"Cron {label} contains unsupported text")
        if start < low or end > high or start > end:
            raise CronError(f"Cron {label} must be between {low} and {high}")
        values.update(range(start, end + 1, step))
    if label == "weekday" and 7 in values:
        values.remove(7); values.add(0)
    return frozenset(values)


def parse(expression: str) -> Cron:
    if not isinstance(expression, str) or len(expression) > 120:
        raise CronError("Cron must be a five-field string")
    parts = expression.split()
    if len(parts) != 5:
        raise CronError("Cron must have five fields: minute hour day month weekday")
    parsed = [_field(value, *definition) for value, definition in zip(parts, FIELDS)]
    return Cron(*parsed, day_any=parts[2] == "*", weekday_any=parts[4] == "*")


def timezone(name: str):
    if name in (None, "", "local"):
        return datetime.now().astimezone().tzinfo
    if not isinstance(name, str) or len(name) > 80:
        raise CronError("Time zone must be local or an IANA time-zone name")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise CronError(f"Unknown time zone: {name[:80]}") from None


def next_after(expression: str, after: float, zone: str = "local") -> float:
    rule, tz = parse(expression), timezone(zone)
    # Iterate real minutes, so DST gaps never invent local wall times and repeated
    # wall times remain distinct instants.
    candidate = (int(after) // 60 + 1) * 60
    limit = candidate + 366 * 24 * 60 * 60 * 5
    while candidate <= limit:
        if rule.matches(datetime.fromtimestamp(candidate, tz)):
            return float(candidate)
        candidate += 60
    raise CronError("Cron schedule has no run time in the next five years")
