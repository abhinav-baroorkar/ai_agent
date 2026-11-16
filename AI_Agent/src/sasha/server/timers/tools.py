"""The model's timer tools: small typed fields in, a resolved local time out (design 1).

The model never sends free-text times. Unclear input raises `TimerAsk`: its message
is what the model must turn into a question (never a default). Refusals (limits, a
"no" to the spoken confirm) raise `TimerRefused`. Both are `ToolError`s, so the model
sees the message; the wake policy tells them apart for the truthful reply.

`source="wake"` builds the wake-turn variant (design 5, D4): items are marked
voice-made, `list_timers` leaves reminder text out (anyone in earshot hears the
answer), and cancelling or snoozing "all" or an item made in a chat needs the spoken
confirm (`confirm(action) -> bool`, the listener's existing flow), unless they ring.

`said` gives the turn's own user text: a period, clock time or delay the words don't
support is dropped or asked about (server/timers/said.py).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sasha.server.timers.model import REPEATS, TimerItem, clean_text, spoken_time
from sasha.server.timers.resolve import (
    Ask,
    Resolved,
    clock_time,
    duration_s,
    next_repeat,
    whole,
)
from sasha.server.timers.said_check import SaidCheck, UserTurn
from sasha.server.timers.service import TimerError, TimerService
from sasha.tools.base import Tool, ToolError, ToolRegistry

TIMER_TOOLS = (
    "set_timer", "set_alarm", "set_reminder", "list_timers", "cancel_timer", "snooze_timer",
)  # fmt: skip
MIN_TIMER_S = 5
# The model sometimes puts the repeat in `day` (bench: day="weekdays"); read it so.
DAY_REPEATS = {
    "weekdays": "weekdays", "every weekday": "weekdays", "weekends": "weekends",
    "every weekend": "weekends", "daily": "daily", "every day": "daily", "everyday": "daily",
}  # fmt: skip
_INT = {"type": "integer"}
_STR = {"type": "string"}
_PERIOD = {"type": "string", "enum": ["am", "pm"], "description": "Only if the person said it"}
_DAY = {
    "type": "string",
    "description": "today, tomorrow, a weekday (monday...), a day of the month (30th) or "
    "YYYY-MM-DD; omit if not said",
}


class TimerAsk(ToolError):
    """The request is unclear: ask the person (the message says what to ask)."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.asks = reason  # the server's own question comes from this (truth.py)


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
        said: UserTurn | None = None,
    ) -> None:
        self.service = service
        self.source = source
        self.confirm = confirm
        # The turn's own user text, checked against the fields (server/timers/said.py).
        cfg = service.config
        self.check = SaidCheck(said, cfg.cues.values(), cfg.answer_window_s)
        self.max_text = service.config.max_text

    # --- helpers -------------------------------------------------------------
    def _guarded(self, fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        def run(**kwargs: Any) -> dict[str, Any]:
            try:
                return fn(**kwargs)
            except Ask as exc:
                self.check.asked()  # S1: the answer is checked with this turn's words
                self.service.note_error(exc.reason, self.source)
                raise TimerAsk(str(exc), exc.reason) from None
            except TimerError as exc:
                self.service.note_error(exc.reason, self.source)
                raise TimerRefused(str(exc), exc.reason) from None

        return run

    def _add(self, item: TimerItem) -> TimerItem:
        item.created_by = self.source
        added = self.service.add(item)
        self.check.done()
        return added

    def _when(self, resolved: Resolved) -> dict[str, Any]:
        out: dict[str, Any] = {"at": spoken_time(resolved.when)}
        if resolved.moved_to_tomorrow:
            out["note"] = "That time has passed today, so it is set for tomorrow; say so."
        return out

    def _approve(self, items: list[TimerItem], verb: str) -> None:
        """D4: a wake turn needs the spoken yes for "all" or a chat-made item. Not for
        stopping what is ringing now, anywhere, one item or several (T10): they are
        demanding attention, so "<name>, stop" just works."""
        if self.source != "wake":
            return
        if len(items) <= 1 and all(i.created_by == "wake" for i in items):
            return
        if items and all(i.state in ("ringing", "pending") for i in items):
            return
        what = f"all {len(items)} timers" if len(items) > 1 else f"the {items[0].kind}"
        if self.confirm is None or not self.confirm(f"{verb} {what}"):
            raise TimerError("Not confirmed; nothing was changed.", "confirm_no")

    def _pick(self, which: str, states: tuple[str, ...]) -> list[TimerItem]:
        service = self.service
        key = clean_text(which, self.max_text).strip()
        with service.lock:
            active = [i for i in service.items.values() if i.state in states]
            if not active and (not key or key.lower() == "all"):
                raise Ask("There are no timers to change. Say so.", "none_active")
            if key.lower() == "all":
                return active
            if not key:
                ringing = [i for i in active if i.state in ("ringing", "pending")]
                if ringing:  # "stop": everything ringing now (T10)
                    return ringing
                if len(active) == 1:
                    return active
                raise Ask("Which one? Call list_timers and ask the person.", "ambiguous_item")
            found = service.find(key, states)
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
        label = self.check.label(clean_text(label, self.max_text))  # not "ten minutes"
        total = duration_s(hours, minutes, seconds)
        self.check.delay(total, label)  # S2: "a timer for 2 eggs" says no length
        if not MIN_TIMER_S <= total <= self.service.config.max_timer_s:
            raise Ask("A timer is 5 seconds to 24 hours. Ask for another length.", "bad_value")
        due = self.service.clock() + total
        item = self._add(TimerItem("", "timer", due, label=label))
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
        said_day = str(day or "").strip().lower()
        if said_day in DAY_REPEATS and repeat in ("none", DAY_REPEATS[said_day]):
            repeat, day = DAY_REPEATS[said_day], ""  # day="weekdays": that's a repeat
        if repeat not in REPEATS:
            raise Ask("repeat must be none, daily, weekdays or weekends", "bad_value")
        if repeat != "none" and day:
            raise Ask("A repeating alarm takes no day. Ask if it should repeat.", "bad_day")
        now = self.service.now_local()
        label = self.check.label(clean_text(label, self.max_text))
        hour, period = self.check.clock(hour, period, label)
        resolved = clock_time(now, hour, minute, period, day, alarm=True)  # R1
        when = resolved.when
        h = resolved.hour if resolved.hour is not None else when.hour  # as asked (DST nit)
        m = resolved.minute if resolved.minute is not None else when.minute
        if repeat != "none":
            when = next_repeat(now, h, m, repeat)
            resolved = Resolved(when, resolved.moved_to_tomorrow and when.date() > now.date())
        item = TimerItem("", "alarm", when.timestamp(), label=label, repeat=repeat, hour=h,
                         minute=m)  # fmt: skip
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
        if relative:  # T8: real seconds from now, like set_timer (not wall-clock time)
            delay = duration_s(in_hours, in_minutes, 0)
            self.check.delay(delay, words)  # S2: the reminder's own words aren't the delay
            due = self.service.clock() + delay
            resolved = Resolved(datetime.fromtimestamp(due, self.service.zone))
        else:
            hour, period = self.check.clock(hour, period, words)  # S2, S4: nor its time
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
                entry = {"id": i.id, "kind": i.kind, "state": i.state, "at": at}
                if i.kind == "timer" and i.state == "scheduled":
                    entry["left"] = _in_words(max(0.0, i.due - service.clock()))
                if self.source != "wake":  # user text isn't read out to the room
                    entry["label"], entry["text"] = i.label, i.text
                elif i.created_by == "wake":  # a voice-made label was said aloud anyway
                    entry["label"] = i.label
                out.append(entry)
        return {"now": spoken_time(service.now_local()), "items": out}

    def cancel_timer(self, which: Any = "") -> dict[str, Any]:
        """Cancel (or, if it is ringing, dismiss) one item, or "all"."""
        items = self._pick(str(which or ""), ("scheduled", "ringing", "pending"))
        self._approve(items, "cancel")
        stopped, failed = [], 0
        for item in items:  # per item: one that ended meanwhile doesn't undo the rest
            try:
                if item.state in ("ringing", "pending"):
                    self.service.dismiss(item.id)
                else:
                    self.service.cancel(item.id)
                stopped.append({"kind": item.kind, "label": item.label})
            except TimerError:
                failed += 1
        if not stopped:
            raise TimerError("It has already ended.", "not_active")
        return {"ok": True, "stopped": stopped, "already_ended": failed}

    def snooze_timer(self, which: Any = "", minutes: Any = None) -> dict[str, Any]:
        # T3: a missed timer or reminder can be snoozed from a chat ("again in 10").
        items = self._pick(str(which or ""), ("ringing", "pending", "missed"))
        self._approve(items, "snooze")
        mins = whole(minutes, "minutes", 1, 60) if minutes not in (None, "") else None
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
                 "period only if the person said morning/evening/am/pm. For a date, call "
                 "get_current_time first. If the tool asks, ask the person; never guess.",
                 {"type": "object", "properties": {"hour": _INT, "minute": _INT,
                  "period": _PERIOD, "day": _DAY, "repeat": {"type": "string",
                  "enum": list(REPEATS)}, "label": _STR}, "required": ["hour"]},
                 g(self.set_alarm)),
            Tool("set_reminder", "Remind the person of something: either in_hours/in_minutes "
                 "from now, or a clock time (hour, minute, period, day). Not both. If the "
                 "person said no time, ask them when; never pick a time yourself.",
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
    said: UserTurn | None = None,
) -> ToolRegistry:
    """`registry` with this source's timer tools (any earlier timer tools replaced).
    `said` gives the turn's user text; every real caller passes it."""
    out = ToolRegistry(registry.get(n) for n in registry.names if n not in TIMER_TOOLS)
    for tool in TimerTools(service, source, confirm, said).tools():
        out.register(tool)
    return out
