"""Turn the model's structured time fields into an absolute local time.

The model never sends free-text times; it fills hours/minutes/period/day. Anything
ambiguous raises `Ask` with the question the model must put to the person (design
section 1: ask, don't guess):
- hour 1-12 without am/pm when both readings are still possible; for alarms, always
  (R1: at 08:00, "7:30" must not silently become 19:30 today);
- no hour at all ("tomorrow morning"); a number that isn't whole ("7.9");
- a day that isn't today/tomorrow/a weekday/YYYY-MM-DD/a day of the month ("30th")/a
  date in words ("2nd of April", "April 2nd");
- "next <weekday>" within 2 days ("next Friday" on a Thursday): this one or the one
  after? (owner rule, 2026-11-16). Further ahead it is the coming one;
- a time already past on a named day (the message says what today is).
A past clock time today without a day moves to tomorrow (`moved` says so).
Unambiguous slips are read, not asked: "18" with "pm" is 18:00, "0" with "am" 00:00.
DST: times are built in the local zone and normalised through UTC, so a time that
doesn't exist (spring forward) moves to the next real one; `hour`/`minute` keep what
was asked for, so a repeating 02:30 alarm still rings at 02:30 on other days.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_ORDINAL = re.compile(r"^(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)$")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december")  # fmt: skip
_SHORT = {m[:3]: i for i, m in enumerate(MONTHS, 1)} | {"sept": 9}
_DAY_MONTH = re.compile(r"^(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+([a-z]+)$")
_MONTH_DAY = re.compile(r"^([a-z]+)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?$")
NEXT_ASK_DAYS = 2  # "next <weekday>" this close is asked back (owner rule)


class Ask(ValueError):
    """The time is unclear: `message` is what the model should ask; `reason` a label."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Resolved:
    when: datetime  # aware, local zone (normalised)
    moved_to_tomorrow: bool = False
    hour: int | None = None  # what was asked for, 24 h (before DST normalising)
    minute: int | None = None


def whole(value: object, name: str, low: int, high: int) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise Ask(f"{name} must be a whole number", "bad_value")
    try:
        number = float(str(value))
    except ValueError:
        raise Ask(f"{name} must be a whole number", "bad_value") from None
    if not math.isfinite(number) or not number.is_integer():  # "inf", "7.9": ask
        raise Ask(f"{name} must be a whole number", "bad_value")
    if not low <= number <= high:
        raise Ask(f"{name} must be between {low} and {high}", "bad_value")
    return int(number)


def duration_s(hours: object, minutes: object, seconds: object) -> int:
    total = (
        whole(hours, "hours", 0, 24) * 3600
        + whole(minutes, "minutes", 0, 24 * 60) * 60
        + whole(seconds, "seconds", 0, 24 * 3600)
    )
    if total <= 0:
        raise Ask("How long should it be? Ask for the duration.", "missing_time")
    return total


def local_at(day: date, hour: int, minute: int, zone: tzinfo) -> datetime:
    naive = datetime(day.year, day.month, day.day, hour, minute)
    aware = naive.replace(tzinfo=zone)
    return aware.astimezone(timezone.utc).astimezone(zone)  # normalise DST gaps


def _month_day(number: int, today: date) -> date:
    """ "the 30th": this month if still ahead (or today), else next month."""
    year, month = today.year, today.month
    for _ in range(3):
        try:
            candidate = date(year, month, number)
        except ValueError:  # no 30th in February: try the next month
            candidate = None
        if candidate is not None and candidate >= today:
            return candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    raise Ask("Which day? Ask for the date.", "bad_day")


def _month(word: str) -> int | None:
    if word in MONTHS:
        return MONTHS.index(word) + 1
    return _SHORT.get(word)


def _dated(number: int, month: int, today: date) -> date:
    """ "2nd of April": this year if still ahead (or today), else next year."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, number)
        except ValueError:  # "30th of February"
            raise Ask("That date doesn't exist. Ask for the date.", "bad_day") from None
        if candidate >= today:
            return candidate
    raise Ask("Which day? Ask for the date.", "bad_day")  # unreachable


def _weekday(name: str, today: date) -> date:
    ahead = (WEEKDAYS.index(name) - today.weekday()) % 7
    return today + timedelta(days=ahead or 7)  # "monday" said on a Monday: next one


def _day(spec: object, today: date) -> date | None:
    """None = no day given."""
    text = " ".join(str(spec or "").strip().lower().replace(",", " ").split())
    if not text:
        return None
    if text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    words = text.split()
    if text in WEEKDAYS or (len(words) == 2 and words[0] == "this" and words[1] in WEEKDAYS):
        return _weekday(words[-1], today)  # "friday", "this friday": the coming one
    if len(words) == 3 and words[0] in WEEKDAYS and words[1:] == ["after", "next"]:
        return _weekday(words[0], today) + timedelta(days=7)  # "friday after next"
    if len(words) == 2 and words[0] == "next" and words[1] in WEEKDAYS:
        coming = _weekday(words[1], today)
        if (coming - today).days <= NEXT_ASK_DAYS:  # "next friday" on a thursday
            later = coming + timedelta(days=7)
            raise Ask(
                f"This {words[1]} ({coming.isoformat()}) or the one after "
                f"({later.isoformat()})? Ask, then send 'this {words[1]}' or "
                f"'{words[1]} after next'.",
                f"next_weekday:{words[1]}",
            )
        return coming
    if m := _ORDINAL.match(text):
        return _month_day(int(m.group(1)), today)
    for pattern, number_at, month_at in ((_DAY_MONTH, 1, 2), (_MONTH_DAY, 2, 1)):
        if (m := pattern.match(text)) and (month := _month(m.group(month_at))):
            return _dated(int(m.group(number_at)), month, today)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise Ask("Which day? Ask: today, tomorrow, a weekday or a date.", "bad_day") from None


def _hour(h: int, period: str) -> list[int]:
    """The possible 24 h readings of an hour."""
    if period == "am" and 1 <= h <= 12:
        return [h % 12]
    if period == "pm" and 1 <= h <= 12:
        return [h % 12 + 12]
    if (period == "am" and h == 0) or (period == "pm" and 13 <= h <= 23):
        return [h]  # "0 am", "18 pm": a slip, but only one reading
    if period:
        raise Ask(f"{h} {period} isn't a time. Ask for the time again.", "bad_value")
    return [h % 12, h % 12 + 12] if 1 <= h <= 12 else [h]  # 7 -> 07 or 19; 12 -> 00 or 12


def clock_time(
    now: datetime,
    hour: object,
    minute: object,
    period: object,
    day: object,
    alarm: bool = False,
) -> Resolved:
    """An alarm or reminder at a clock time. `now` is aware, in the local zone."""
    if hour is None or hour == "":
        raise Ask("At what time? Ask for the hour (e.g. 7:30 in the morning).", "missing_time")
    h = whole(hour, "hour", 0, 23)
    m = whole(minute, "minute", 0, 59)
    when_day = _day(day, now.date())
    p = str(period or "").strip().lower()
    if p not in ("", "am", "pm"):
        raise Ask("period must be am or pm", "bad_value")
    readings = _hour(h, p)
    zone = now.tzinfo
    assert zone is not None
    ask = Ask(f"{h}:{m:02d} in the morning or in the evening? Ask.", "ambiguous_period")
    if len(readings) > 1 and (alarm or when_day is not None):
        raise ask
    if when_day is not None:
        when = local_at(when_day, readings[0], m, zone)
        if when <= now:
            today = now.strftime("%A %d %B %Y")
            raise Ask(f"That is in the past (today is {today}). Ask for a later time.", "past")
        return Resolved(when, hour=readings[0], minute=m)
    today_times = [local_at(now.date(), r, m, zone) for r in readings]
    ahead = [(t, r) for t, r in zip(today_times, readings, strict=True) if t > now]
    if len(ahead) == 1:
        return Resolved(ahead[0][0], hour=ahead[0][1], minute=m)
    if len(readings) > 1:  # both ahead, or both passed (tomorrow, but which?)
        raise ask
    tomorrow = local_at(now.date() + timedelta(days=1), readings[0], m, zone)
    return Resolved(tomorrow, True, hour=readings[0], minute=m)


def next_repeat(after: datetime, hour: int, minute: int, repeat: str) -> datetime:
    """The next occurrence strictly after `after` for a repeating alarm."""
    zone = after.tzinfo
    assert zone is not None
    day = after.date()
    for _ in range(8):
        candidate = local_at(day, hour, minute, zone)
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
