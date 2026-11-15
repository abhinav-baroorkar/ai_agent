"""Timer items and how they look in the API (contract v0.3 `Timer`)."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KINDS = ("timer", "alarm", "reminder")
REPEATS = ("none", "daily", "weekdays", "weekends")
# scheduled -> ringing -> done | missed ; scheduled -> pending (nobody connected) -> ...
STATES = ("scheduled", "ringing", "pending", "missed", "done")
ACTIVE = ("scheduled", "ringing", "pending")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def clean_text(value: Any, limit: int) -> str:
    """User text (labels, reminders): control characters out, length capped."""
    return _CONTROL.sub(" ", str(value or "")).strip()[:limit]


def local_zone(name: str = "") -> tzinfo:
    """The configured zone, else the system's (TZ, /etc/timezone, /etc/localtime)."""
    for candidate in (name, os.environ.get("TZ", "")):
        if candidate:
            try:
                return ZoneInfo(candidate.lstrip(":"))
            except (ZoneInfoNotFoundError, ValueError):
                pass
    try:
        key = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        return ZoneInfo(key)
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return ZoneInfo(link.split("zoneinfo/", 1)[1])
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    return datetime.now().astimezone().tzinfo or timezone.utc


@dataclass
class TimerItem:
    id: str
    kind: str
    due: float  # UTC epoch seconds
    created_by: str = "chat"  # chat | wake
    label: str = ""
    text: str = ""
    repeat: str = "none"
    state: str = "scheduled"
    created: float = 0.0
    hour: int | None = None  # alarms keep their local time, to re-resolve repeats/zones
    minute: int | None = None
    late_s: float = 0.0
    snoozes: int = 0
    bare_snoozes: int = 0
    rang_at: float | None = None
    ended_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    def to_store(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_store(cls, raw: dict[str, Any]) -> TimerItem | None:
        try:
            item = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        except TypeError:
            return None
        if item.kind not in KINDS or item.state not in STATES or item.repeat not in REPEATS:
            return None
        return item

    def to_api(self, zone: tzinfo) -> dict[str, Any]:
        """The contract's `Timer` (label and text are plain user text)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "text": self.text,
            "due_at": datetime.fromtimestamp(self.due, zone).isoformat(timespec="seconds"),
            "repeat": self.repeat,
            "state": self.state,
            "late_s": round(self.late_s),
            "snoozes": self.snoozes,
            "created_by": self.created_by,
        }


def iso_now(now: float, zone: tzinfo) -> str:
    return datetime.fromtimestamp(now, zone).isoformat(timespec="seconds")


def spoken_time(when: datetime) -> str:
    """e.g. "Sat 15 Nov, 07:30" (what the tool result echoes back)."""
    return when.strftime("%a %d %b, %H:%M")
