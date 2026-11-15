"""The model's timer tools: small typed fields in, a resolved local time out (design 1).

The model never sends free-text times. Unclear input raises `TimerAsk`: its message
is what the model must turn into a question (never a default). Refusals (limits, a
"no" to the spoken confirm) raise `TimerRefused`. Both are `ToolError`s, so the model
sees the message; the wake policy tells them apart for the truthful reply.

`source="wake"` builds the wake-turn variant (design 5, D4): items are marked
voice-made, `list_timers` leaves reminder text out (anyone in earshot hears the
answer), and cancelling or snoozing "all" or an item made in a chat needs the spoken
confirm (`confirm(action) -> bool`, the listener's existing flow).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sasha.server.timers.model import REPEATS, TimerItem, clean_text, spoken_time
from sasha.server.timers.resolve import Ask, Resolved, clock_time, duration_s, next_repeat
from sasha.server.timers.service import TimerError, TimerService
from sasha.tools.base import Tool, ToolError, ToolRegistry

TIMER_TOOLS = (
    "set_timer", "set_alarm", "set_reminder", "list_timers", "cancel_timer", "snooze_timer",
)  # fmt: skip
MIN_TIMER_S = 5
_INT = {"type": "integer"}
_STR = {"type": "string"}
_PERIOD = {"type": "string", "enum": ["am", "pm"], "description": "Only if the person said it"}
_DAY = {
    "type": "string",
    "description": "today, tomorrow, a weekday (monday...) or YYYY-MM-DD; omit if not said",
}


class TimerAsk(ToolError):
    """The request is unclear: ask the person (the message says what to ask)."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class TimerRefused(ToolError):
    """Refused (a limit, or no to the spoken confirm): nothing was changed."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _in_words(seconds: float) -> str:
    seconds = round(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    parts = [
        f"{v} {n}{'' if v == 1 else 's'}"
        for v, n in ((h, "hour"), (m, "minute"), (s, "second"))
        if v
    ]
    return " ".join(parts) or "0 seconds"


class TimerTools:
    def __init__(
        self,
        service: TimerService,
        source: str = "chat",
        confirm: Callable[[str], bool] | None = None,
    ) -> None:
        self.service = service
        self.source = source
        self.confirm = confirm
        self.max_text = service.config.max_text

    # --- helpers -------------------------------------------------------------
    def _guarded(self, fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        def run(**kwargs: Any) -> dict[str, Any]:
            try:
                return fn(**kwargs)
            except Ask as exc:
                self.service.note_error(exc.reason, self.source)
                raise TimerAsk(str(exc), exc.reason) from None
            except TimerError as exc:
                self.service.note_error(exc.reason, self.source)
                raise TimerRefused(str(exc), exc.reason) from None

        return run

    def _add(self, item: TimerItem) -> TimerItem:
        item.created_by = self.source
        return self.service.add(item)

    def _when(self, resolved: Resolved) -> dict[str, Any]:
        out: dict[str, Any] = {"at": spoken_time(resolved.when)}
        if resolved.moved_to_tomorrow:
            out["note"] = "That time has passed today, so it is set for tomorrow; say so."
        return out

    def _approve(self, items: list[TimerItem], verb: str) -> None:
        """D4: a wake turn needs the spoken yes for "all" or a chat-made item."""
        if self.source != "wake":
            return
        if len(items) <= 1 and all(i.created_by == "wake" for i in items):
            return
        what = f"all {len(items)} timers" if len(items) > 1 else f"the {items[0].kind}"
        if self.confirm is None or not self.confirm(f"{verb} {what}"):
            raise TimerError("Not confirmed; nothing was changed.", "confirm_no")

    def _pick(self, which: str, states: tuple[str, ...]) -> list[TimerItem]:
        service = self.service
        key = clean_text(which, self.max_text).strip()
        with service.lock:
            active = [i for i in service.items.values() if i.state in states]
            if key.lower() == "all":
                if not active:
                    raise Ask("There are no timers to change. Say so.", "unknown")
                return active
            if not key:
                ringing = [i for i in active if i.state in ("ringing", "pending")]
                if len(ringing) == 1:
                    return ringing
                if len(active) == 1:
                    return active
                raise Ask("Which one? Call list_timers and ask the person.", "ambiguous_item")
            found = [i for i in service.find(key) if i.state in states]
        if not found:
            raise Ask("No timer matches that. Call list_timers and ask which.", "unknown")
        if len(found) > 1:
            raise Ask(
                "Several match that label. Ask which one (give their times).", "ambiguous_item"
            )
        return found

    # --- the tools -------------------------------------------------------------
    def set_timer(
        self, hours: Any = 0, minutes: Any = 0, seconds: Any = 0, label: Any = ""
    ) -> dict[str, Any]:
        total = duration_s(hours, minutes, seconds)
        if not MIN_TIMER_S <= total <= self.service.config.max_timer_s:
            raise Ask("A timer is 5 seconds to 24 hours. Ask for another length.", "bad_value")
        due = self.service.clock() + total
        item = self._add(TimerItem("", "timer", due, label=clean_text(label, self.max_text)))
        ends = datetime.fromtimestamp(due, self.service.zone)
        return {"ok": True, "id": item.id, "kind": "timer", "length": _in_words(total),
                "ends_at": spoken_time(ends)}  # fmt: skip

    def set_alarm(
        self,
        hour: Any = None,
        minute: Any = 0,
        period: Any = "",
        day: Any = "",
        repeat: Any = "none",
        label: Any = "",
    ) -> dict[str, Any]:
        repeat = str(repeat or "none").strip().lower()
        if repeat not in REPEATS:
            raise Ask("repeat must be none, daily, weekdays or weekends", "bad_value")
        if repeat != "none" and day:
            raise Ask("A repeating alarm takes no day. Ask if it should repeat.", "bad_day")
        now = self.service.now_local()
        resolved = clock_time(now, hour, minute, period, day)
        when = resolved.when
        if repeat != "none":
            when = next_repeat(now, when.hour, when.minute, repeat)
            resolved = Resolved(when, resolved.moved_to_tomorrow and when.date() > now.date())
        item = TimerItem("", "alarm", when.timestamp(), label=clean_text(label, self.max_text),
                         repeat=repeat, hour=when.hour, minute=when.minute)  # fmt: skip
        item = self._add(item)
        return {
            "ok": True,
            "id": item.id,
            "kind": "alarm",
            "repeat": repeat,
            **self._when(resolved),
        }

    def set_reminder(
        self,
        text: Any = "",
        in_hours: Any = None,
        in_minutes: Any = None,
        hour: Any = None,
        minute: Any = 0,
        period: Any = "",
        day: Any = "",
    ) -> dict[str, Any]:
        words = clean_text(text, self.max_text).strip()
        if not words:
            raise Ask("What should the reminder say? Ask.", "missing_text")
        relative = in_hours not in (None, "", 0) or in_minutes not in (None, "", 0)
        if relative and hour not in (None, ""):
            raise Ask("Give either in_hours/in_minutes or a clock time, not both.", "bad_value")
        now = self.service.now_local()
        if relative:
            resolved = Resolved(now + timedelta(seconds=duration_s(in_hours, in_minutes, 0)))
        else:
            resolved = clock_time(now, hour, minute, period, day)
        item = self._add(TimerItem("", "reminder", resolved.when.timestamp(), text=words))
        return {"ok": True, "id": item.id, "kind": "reminder", **self._when(resolved)}

    def list_timers(self) -> dict[str, Any]:
        service = self.service
        with service.lock:
            items = sorted((i for i in service.items.values() if i.active), key=lambda i: i.due)
            out = []
            for i in items:
                at = spoken_time(datetime.fromtimestamp(i.due, service.zone))
                entry = {"id": i.id, "kind": i.kind, "label": i.label, "state": i.state, "at": at}
                if i.kind == "timer" and i.state == "scheduled":
                    entry["left"] = _in_words(max(0.0, i.due - service.clock()))
                if self.source != "wake":  # reminder text isn't read out to the room
                    entry["text"] = i.text
                out.append(entry)
        return {"now": spoken_time(service.now_local()), "items": out}

    def cancel_timer(self, which: Any = "") -> dict[str, Any]:
        """Cancel (or, if it is ringing, dismiss) one item, or "all"."""
        items = self._pick(str(which or ""), ("scheduled", "ringing", "pending"))
        self._approve(items, "cancel")
        for item in items:
            if item.state in ("ringing", "pending"):
                self.service.dismiss(item.id)
            else:
                self.service.cancel(item.id)
        return {"ok": True, "stopped": [{"kind": i.kind, "label": i.label} for i in items]}

    def snooze_timer(self, which: Any = "", minutes: Any = None) -> dict[str, Any]:
        items = self._pick(str(which or ""), ("ringing", "pending"))
        self._approve(items, "snooze")
        try:
            mins = int(minutes) if minutes not in (None, "") else None
        except (TypeError, ValueError):
            raise Ask("minutes must be a whole number from 1 to 60", "bad_value") from None
        done = [self.service.snooze(i.id, mins) for i in items]
        due = datetime.fromtimestamp(done[0].due, self.service.zone)
        return {"ok": True, "count": len(done), "rings_again_at": spoken_time(due)}

    # --- specs -------------------------------------------------------------------
    def tools(self) -> list[Tool]:
        g = self._guarded
        return [
            Tool("set_timer", "Start a countdown timer. Use separate whole numbers: "
                 "'ten minutes' is minutes=10.",
                 {"type": "object", "properties": {"hours": _INT, "minutes": _INT,
                  "seconds": _INT, "label": _STR}}, g(self.set_timer)),
            Tool("set_alarm", "Set an alarm at a clock time. Give hour and minute as said; "
                 "period only if the person said morning/evening/am/pm. If the tool asks, "
                 "ask the person; never guess.",
                 {"type": "object", "properties": {"hour": _INT, "minute": _INT,
                  "period": _PERIOD, "day": _DAY, "repeat": {"type": "string",
                  "enum": list(REPEATS)}, "label": _STR}, "required": ["hour"]},
                 g(self.set_alarm)),
            Tool("set_reminder", "Remind the person of something: either in_hours/in_minutes "
                 "from now, or a clock time (hour, minute, period, day). Not both.",
                 {"type": "object", "properties": {"text": _STR, "in_hours": _INT,
                  "in_minutes": _INT, "hour": _INT, "minute": _INT, "period": _PERIOD,
                  "day": _DAY}, "required": ["text"]}, g(self.set_reminder)),
            Tool("list_timers", "The active timers, alarms and reminders.",
                 {"type": "object", "properties": {}}, g(self.list_timers)),
            Tool("cancel_timer", "Cancel or stop a timer, alarm or reminder: its id, its "
                 "label, or 'all'. Empty = the one ringing now.",
                 {"type": "object", "properties": {"which": _STR}}, g(self.cancel_timer)),
            Tool("snooze_timer", "Snooze the ringing item (or the one named) for some "
                 "minutes (default 5).",
                 {"type": "object", "properties": {"which": _STR, "minutes": _INT}},
                 g(self.snooze_timer)),
        ]  # fmt: skip


def add_timer_tools(
    registry: ToolRegistry,
    service: TimerService,
    source: str = "chat",
    confirm: Callable[[str], bool] | None = None,
) -> ToolRegistry:
    """`registry` with this source's timer tools (any earlier timer tools replaced)."""
    out = ToolRegistry(registry.get(n) for n in registry.names if n not in TIMER_TOOLS)
    for tool in TimerTools(service, source, confirm).tools():
        out.register(tool)
    return out
