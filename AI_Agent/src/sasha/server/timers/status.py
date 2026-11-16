"""The real state of the timers, in a fixed form the server can say itself.

A status question ("is my alarm set?") answered without `list_timers` gets this line
instead of the model's guess (truth.py): the server knows the truth, so it says it.
Reminder text is never read out (as on wake turns): only the kind and the time.
"""

from __future__ import annotations

from datetime import datetime

from sasha.config_timers import TimersConfig
from sasha.server.timers.model import in_words
from sasha.server.timers.service import TimerService

MAX_ITEMS = 4  # then "And 3 more."
KIND_WORDS = {
    "timer": "timer", "timers": "timer", "countdown": "timer",
    "alarm": "alarm", "alarms": "alarm",
    "reminder": "reminder", "reminders": "reminder", "remind": "reminder",
}  # fmt: skip


def kinds_asked(words: list[str]) -> set[str]:
    """The kinds a question names ("is my alarm set?": alarm); none named: all."""
    return {KIND_WORDS[w] for w in words if w in KIND_WORDS} or {"timer", "alarm", "reminder"}


def clock_words(when: datetime, now: datetime, cfg: TimersConfig) -> str:
    """e.g. "7:00 AM tomorrow", "6:30 PM today", "8:00 AM Monday", "9:00 AM 2 April"."""
    hour = when.hour % 12 or 12
    clock = f"{hour}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"
    days = (when.date() - now.date()).days
    if days == 0:
        day = cfg.status_today
    elif days == 1:
        day = cfg.status_tomorrow
    elif days < 7:
        day = when.strftime("%A")
    else:
        day = f"{when.day} {when.strftime('%B')}"
    return f"{clock} {day}"


def status_line(service: TimerService, kinds: set[str]) -> str:
    cfg = service.config
    now = service.now_local()
    with service.lock:
        items = sorted(
            (i for i in service.items.values() if i.active and i.kind in kinds),
            key=lambda i: i.due,
        )
    if not items:
        key = next(iter(kinds)) if len(kinds) == 1 else "all"
        return cfg.status_none[key]
    parts = []
    for item in items[:MAX_ITEMS]:
        name = cfg.status_kinds[item.kind]
        if item.state in ("ringing", "pending"):
            parts.append(cfg.status_ringing.format(kind=name))
        elif item.kind == "timer":
            left = in_words(max(0.0, item.due - service.clock()))
            parts.append(cfg.status_left.format(kind=name, left=left))
        else:
            when = datetime.fromtimestamp(item.due, service.zone)
            parts.append(cfg.status_at.format(kind=name, when=clock_words(when, now, cfg)))
    if len(items) > MAX_ITEMS:
        parts.append(cfg.status_more.format(count=len(items) - MAX_ITEMS))
    return " ".join(parts)
