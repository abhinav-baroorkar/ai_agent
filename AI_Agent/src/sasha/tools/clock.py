"""A read-only clock tool, so the model never has to guess the date or time.

Every bot gets it (server and CLI); wake turns may use it too (it changes nothing).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sasha.tools.base import Tool

CLOCK_TOOL = "get_current_time"


def _local_now() -> datetime:
    return datetime.now().astimezone()


def clock_tool(now: Callable[[], datetime] = _local_now) -> Tool:
    def get_current_time() -> dict[str, Any]:
        t = now()
        return {
            "date": t.date().isoformat(),
            "time": t.strftime("%H:%M"),
            "weekday": t.strftime("%A"),
            "timezone": t.tzname() or "",
        }

    return Tool(
        CLOCK_TOOL,
        "The current local date, time, weekday and time zone. Call it whenever the "
        "time or date matters; never guess them.",
        {"type": "object", "properties": {}},
        get_current_time,
    )
