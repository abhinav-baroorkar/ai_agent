"""Turn the model's structured time fields into an absolute local time.

The model never sends free-text times; it fills hours/minutes/period/day. Anything
ambiguous raises `Ask` with the question the model must put to the person (design
section 1: ask, don't guess):
- hour 1-12 without am/pm when both readings are still possible;
- no hour at all ("tomorrow morning");
- a day that isn't today/tomorrow/a weekday/YYYY-MM-DD.
A past clock time today without a day moves to tomorrow (`moved` says so).
DST: times are built in the local zone and normalised through UTC, so a time that
doesn't exist (spring forward) moves to the next real one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class Ask(ValueError):
    """The time is unclear: `message` is what the model should ask; `reason` a label."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Resolved:
    when: datetime  # aware, local zone
    moved_to_tomorrow: bool = False


def _int(value: object, name: str, low: int, high: int) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise Ask(f"{name} must be a whole number", "bad_value")
    try:
        number = int(float(str(value)))
    except ValueError:
        raise Ask(f"{name} must be a whole number", "bad_value") from None
    if not low <= number <= high:
        raise Ask(f"{name} must be between {low} and {high}", "bad_value")
    return number


def duration_s(hours: object, minutes: object, seconds: object) -> int:
    total = (
        _int(hours, "hours", 0, 24) * 3600
        + _int(minutes, "minutes", 0, 24 * 60) * 60
        + _int(seconds, "seconds", 0, 24 * 3600)
    )
    if total <= 0:
        raise Ask("How long should it be? Ask for the duration.", "missing_time")
    return total


def _local(day: date, hour: int, minute: int, zone: tzinfo) -> datetime:
    naive = datetime(day.year, day.month, day.day, hour, minute)
    aware = naive.replace(tzinfo=zone)
    return aware.astimezone(timezone.utc).astimezone(zone)  # normalise DST gaps


def _day(spec: object, today: date) -> date | None:
    """None = no day given."""
    text = str(spec or "").strip().lower()
    if not text:
        return None
    if text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text in WEEKDAYS:
        ahead = (WEEKDAYS.index(text) - today.weekday()) % 7
        return today + timedelta(days=ahead or 7)  # "monday" said on a Monday: next one
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise Ask("Which day? Ask: today, tomorrow, a weekday or a date.", "bad_day") from None


def clock_time(
    now: datetime, hour: object, minute: object, period: object, day: object
) -> Resolved:
    """An alarm or reminder at a clock time. `now` is aware, in the local zone."""
    if hour is None or hour == "":
        raise Ask("At what time? Ask for the hour (e.g. 7:30 in the morning).", "missing_time")
    h = _int(hour, "hour", 0, 23)
    m = _int(minute, "minute", 0, 59)
    when_day = _day(day, now.date())
    p = str(period or "").strip().lower()
    if p not in ("", "am", "pm"):
        raise Ask("period must be am or pm", "bad_value")
    if p and not 1 <= h <= 12:
        raise Ask("With am or pm, the hour must be 1 to 12", "bad_value")
    if p:
        h = h % 12 + (12 if p == "pm" else 0)
        readings = [h]
    elif 1 <= h <= 12:
        readings = [h % 12, h % 12 + 12]  # e.g. 7 -> 07 or 19; 12 -> 00 or 12
    else:
        readings = [h]
    zone = now.tzinfo
    assert zone is not None
    if when_day is not None:
        if len(readings) > 1:
            raise Ask(f"{h}:{m:02d} in the morning or in the evening? Ask.", "ambiguous_period")
        when = _local(when_day, readings[0], m, zone)
        if when <= now:
            raise Ask("That time has already passed. Ask for a later time.", "past")
        return Resolved(when)
    today = [_local(now.date(), r, m, zone) for r in readings]
    ahead = [t for t in today if t > now]
    if len(ahead) == 1:
        return Resolved(ahead[0])
    if len(ahead) > 1:
        raise Ask(f"{h}:{m:02d} in the morning or in the evening? Ask.", "ambiguous_period")
    if len(readings) > 1:  # both passed today: tomorrow, but which?
        raise Ask(f"{h}:{m:02d} in the morning or in the evening? Ask.", "ambiguous_period")
    return Resolved(_local(now.date() + timedelta(days=1), readings[0], m, zone), True)


def next_repeat(after: datetime, hour: int, minute: int, repeat: str) -> datetime:
    """The next occurrence strictly after `after` for a repeating alarm."""
    zone = after.tzinfo
    assert zone is not None
    day = after.date()
    for _ in range(8):
        candidate = _local(day, hour, minute, zone)
        weekday = candidate.weekday()
        fits = (
            repeat == "daily"
            or (repeat == "weekdays" and weekday < 5)
            or (repeat == "weekends" and weekday >= 5)
        )
        if fits and candidate > after:
            return candidate
        day += timedelta(days=1)
    raise ValueError(f"no next occurrence for repeat={repeat}")
