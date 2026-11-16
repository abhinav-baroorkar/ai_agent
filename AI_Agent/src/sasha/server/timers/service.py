"""The brain's timers: storage, limits, the scheduler tick, and events to sockets.

Thread-safe: tools call it from LLM worker threads, routes from the threadpool, the
scheduler from its own daemon thread. All state changes go through `self.lock`, are
saved to `Data/state/timers.json`, and are announced to the `Hub` (the connected
sockets), which decides where a ring is heard (server/timers/hub.py).

The scheduler sleeps until the next due time, the next tone, or `check_every_s`, and
re-reads the clock each wake, so suspend, clock jumps and DST are caught (design 2).
"""

from __future__ import annotations

import contextlib
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from sasha.config_timers import TimersConfig
from sasha.server.timers.model import ACTIVE, TimerItem, iso_now, local_zone
from sasha.server.timers.resolve import local_at, next_repeat
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter


class TimerError(ValueError):
    """A refused request (limit, unknown id): the message is shown or said."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class TimerService:
    def __init__(
        self,
        config: TimersConfig,
        store: JsonStore,
        telemetry: TelemetryWriter | None = None,
        clock: Callable[[], float] = time.time,
        zone: tzinfo | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.telemetry = telemetry
        self.clock = clock
        self.lock = threading.RLock()
        # A given zone (tests: an invented one) or a configured name is never re-read.
        self.zone_fixed = zone is not None or bool(config.timezone)
        self.zone = zone or local_zone(config.timezone)
        self.hub: Any = None  # set by the app: server/timers/hub.Hub
        self._wake_made: deque[float] = deque()
        self._next_tone: dict[str, float] = {}
        self._kick = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.items: dict[str, TimerItem] = {}
        for raw in store.load().get("items") or []:
            item = TimerItem.from_store(raw) if isinstance(raw, dict) else None
            if item is not None:
                self.items[item.id] = item
        self.recover()

    # --- time ------------------------------------------------------------------
    def now_local(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), self.zone)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            items = sorted(self.items.values(), key=lambda i: i.due)
            return {
                "now": iso_now(self.clock(), self.zone),
                "timers": [i.to_api(self.zone) for i in items],
            }

    # --- creating ----------------------------------------------------------------
    def _check_limits(self, source: str, due: float) -> None:
        now = self.clock()
        if len([i for i in self.items.values() if i.active]) >= self.config.max_active:
            raise TimerError("Too many timers already; cancel one first.", "limit_active")
        if due - now > self.config.max_ahead_days * 86400:
            raise TimerError("That is too far ahead.", "limit_ahead")
        if source == "wake":
            while self._wake_made and now - self._wake_made[0] >= self.config.wake_window_s:
                self._wake_made.popleft()
            voice_active = [i for i in self.items.values() if i.active and i.created_by == "wake"]
            if (
                len(self._wake_made) >= self.config.wake_max_per_window
                or len(voice_active) >= self.config.wake_max_active
            ):
                raise TimerError("Too many timers by voice; try again later.", "limit_wake")

    def add(self, item: TimerItem) -> TimerItem:
        with self.lock:
            self._check_limits(item.created_by, item.due)
            item.id = item.id or secrets.token_hex(4)
            item.created = self.clock()
            self.items[item.id] = item
            if item.created_by == "wake":
                self._wake_made.append(item.created)
            self._changed("created", item, lead_s=item.due - item.created)
        return item

    # --- stopping ----------------------------------------------------------------
    def get(self, item_id: str) -> TimerItem:
        with self.lock:
            item = self.items.get(item_id)
        if item is None:
            raise TimerError("No such timer.", "unknown")
        return item

    def find(self, key: str, states: tuple[str, ...] = ACTIVE) -> list[TimerItem]:
        """Items in `states` matching an id or a label (case-insensitive)."""
        with self.lock:
            if key in self.items and self.items[key].state in states:
                return [self.items[key]]
            wanted = key.strip().lower()
            items = self.items.values()
            return [i for i in items if i.state in states and i.label.lower() == wanted]

    def cancel(self, item_id: str, how: str = "cancelled") -> TimerItem:
        """Cancel an active item (the model's cancel_timer); ringing ones end too."""
        with self.lock:
            item = self.get(item_id)
            if not item.active:
                raise TimerError("It has already ended.", "not_active")
            self._end(item, "done", how)
            return item

    def remove(self, item_id: str) -> None:
        """`DELETE`: cancel a scheduled item, or remove a missed/done one (contract)."""
        with self.lock:
            item = self.get(item_id)
            if item.state in ("ringing", "pending"):
                raise TimerError("It's ringing: dismiss it instead.", "use_dismiss")
            if item.state == "scheduled":
                self._end(item, "done", "cancelled")
                return
            del self.items[item.id]
            self._changed("removed", item)

    def dismiss(self, item_id: str) -> TimerItem:
        with self.lock:
            item = self.get(item_id)
            if item.state not in ("ringing", "pending", "missed"):
                raise TimerError("It isn't ringing.", "not_ringing")
            self._end(item, "done", "dismissed")
            return item

    def snooze(self, item_id: str, minutes: int | None = None, bare: bool = False) -> TimerItem:
        with self.lock:
            item = self.get(item_id)
            # T3: a missed timer or reminder may be snoozed from the card or a chat,
            # never by a bare stop-window "snooze"; a missed alarm can only be dismissed.
            late_ok = item.state == "missed" and item.kind != "alarm" and not bare
            if item.state not in ("ringing", "pending") and not late_ok:
                raise TimerError("It isn't ringing.", "not_ringing")
            if bare and item.bare_snoozes >= self.config.bare_snooze_max:
                raise TimerError("Say the name or tap to snooze again.", "snooze_limit")
            minutes = minutes or self.config.snooze_minutes
            if not 1 <= int(minutes) <= 60:
                raise TimerError("Snooze is 1 to 60 minutes.", "bad_value")
            was_ringing = item.state in ("ringing", "pending")
            item.snoozes += 1
            item.bare_snoozes += 1 if bare else 0
            item.due = self.clock() + int(minutes) * 60
            item.state, item.ended_at, item.rang_at = "scheduled", None, None
            self._next_tone.pop(item.id, None)
            self._changed("snoozed", item, how="snoozed", was_ringing=was_ringing)
            return item

    def _end(self, item: TimerItem, state: str, how: str) -> None:
        was_ringing = item.state in ("ringing", "pending")
        ring_s = self.clock() - item.rang_at if item.rang_at else 0.0
        self._settle(item, state, keep_repeat=how != "cancelled")
        self._changed(how, item, how=how, ring_s=ring_s, was_ringing=was_ringing)

    def _settle(self, item: TimerItem, state: str, keep_repeat: bool = True) -> bool:
        """End this occurrence (T1): the one place an occurrence ends. A repeating alarm
        is scheduled again at its next occurrence (unless cancelled); anything else
        takes `state`. True if it was rescheduled. The caller saves and announces."""
        self._next_tone.pop(item.id, None)
        if keep_repeat and item.repeat != "none" and item.hour is not None:
            nxt = next_repeat(self.now_local(), item.hour, item.minute or 0, item.repeat)
            item.due, item.state, item.rang_at = nxt.timestamp(), "scheduled", None
            item.late_s, item.snoozes, item.bare_snoozes = 0.0, 0, 0
            return True
        item.state, item.ended_at = state, self.clock()
        return False

    # --- the scheduler -------------------------------------------------------------
    def recover(self) -> None:
        """At start: what came due while the brain was off (design 2, D2)."""
        now, cfg = self.clock(), self.config
        with self.lock:
            for item in self.items.values():
                if item.state == "ringing":
                    item.state = "pending"  # the ring was cut by the restart
                if item.state != "scheduled" or item.due > now:
                    continue
                late = now - item.due
                limit = cfg.alarm_late_s if item.kind == "alarm" else cfg.late_fire_s
                if late <= limit:
                    item.state, item.late_s = "pending", late  # generic line on connect
                else:
                    self._event("missed", item, late_s=late)
                    if not self._settle(item, "missed"):
                        item.late_s = late
            self._save()

    def tick(self) -> float:
        """Fire what is due, ring, time out, clean up. Returns seconds to sleep."""
        now, cfg = self.clock(), self.config
        with self.lock:
            self._rezone(now)
            for item in list(self.items.values()):
                if item.state == "scheduled" and item.due <= now:
                    item.late_s = max(0.0, now - item.due)
                    self._fire(item, now)
                elif item.state == "ringing":
                    ring_for = cfg.alarm_ring_s if item.kind == "alarm" else cfg.ring_s
                    if item.rang_at is not None and now - item.rang_at >= ring_for:
                        self._end(item, "missed", "timeout")
                    elif now >= self._next_tone.get(item.id, now + 1):
                        self._next_tone[item.id] = now + cfg.tone_every_s
                        if self.hub is not None:
                            self.hub.tone(item)
                elif item.state == "pending" and now - item.due > cfg.pending_keep_s:
                    late = now - item.due
                    self._settle(item, "missed")
                    self._changed("missed", item, late_s=late)
                elif not item.active and (item.ended_at or now) < now - cfg.keep_done_days * 86400:
                    del self.items[item.id]
                    self._save()
            return self._sleep_for(now)

    def _fire(self, item: TimerItem, now: float, generic: bool = False) -> None:
        # Ringing before the hub is told, so the socket's re-check (T4) sees it ringing.
        item.state, item.rang_at = "ringing", now
        delivered = self.hub.ring(item, generic) if self.hub is not None else False
        if delivered:
            self._next_tone[item.id] = now + self.config.tone_every_s
        else:  # nobody connected: shown on the next connection
            item.state, item.rang_at = "pending", None
        self._changed("fired" if delivered else "pending", item, late_s=item.late_s)

    def deliver_pending(self) -> list[TimerItem]:
        """A socket connected (T3). Alarms still within their late limit ring now, with
        the generic line only (D2). Every other pending item ends as missed (a repeating
        alarm moves to its next occurrence); those are returned for the generic line."""
        with self.lock:
            now, missed = self.clock(), []
            for item in [i for i in self.items.values() if i.state == "pending"]:
                late = now - item.due
                self._event("pending_delivered", item, late_s=late)
                if item.kind == "alarm" and late <= self.config.alarm_late_s:
                    item.late_s = late
                    self._fire(item, now, generic=True)
                else:
                    if not self._settle(item, "missed"):
                        item.late_s = late
                    missed.append(item)  # a repeating alarm moved on: still said (nit)
            if missed:
                self._save()
                self._broadcast()
            return missed

    def next_tone_in(self, item_id: str) -> float:
        """Seconds until this ringing item's next tone (0 if it has none)."""
        with self.lock:
            at = self._next_tone.get(item_id)
            return max(0.0, at - self.clock()) if at is not None else 0.0

    def _rezone(self, now: float, zone: tzinfo | None = None) -> None:
        """The system zone changed (travel, a settings change): re-resolve alarms."""
        if zone is None:
            if self.zone_fixed:
                return
            zone = local_zone()
            if not isinstance(zone, ZoneInfo):
                return  # only a bare offset is known: it flips at every DST change (T2 nit)
        if str(zone) == str(self.zone):
            return
        old, self.zone = self.zone, zone  # alarms keep their local time; timers their instant
        after = datetime.fromtimestamp(now, zone) - timedelta(seconds=1)
        for item in self.items.values():
            if item.kind != "alarm" or item.state != "scheduled" or item.hour is None:
                continue
            hour, minute = item.hour, item.minute or 0
            if item.snoozes:
                continue  # snoozed: "in 5 minutes" keeps its instant, like a timer
            if item.repeat != "none":  # T2: its own repeat, not "daily"
                item.due = next_repeat(after, hour, minute, item.repeat).timestamp()
            else:  # T2: a one-off keeps its local date
                day = datetime.fromtimestamp(item.due, old).date()
                item.due = local_at(day, hour, minute, zone).timestamp()
        self._changed("rezoned", None)

    def _sleep_for(self, now: float) -> float:
        times = [i.due for i in self.items.values() if i.state == "scheduled"]
        times += list(self._next_tone.values())
        nearest = min(times, default=now + self.config.check_every_s)
        return max(0.05, min(self.config.check_every_s, nearest - now))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="timers", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()

    def kick(self) -> None:
        self._kick.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                wait = self.tick()
            except Exception:  # never let the scheduler thread die
                wait = self.config.check_every_s
            self._kick.wait(wait)
            self._kick.clear()

    # --- persistence, events, telemetry ------------------------------------------
    def _save(self) -> None:
        self.store.save({"items": [i.to_store() for i in self.items.values()]})

    def _broadcast(self) -> None:
        if self.hub is not None:
            self.hub.broadcast(self.snapshot())

    def _changed(self, event: str, item: TimerItem | None, **numbers: Any) -> None:
        self._save()
        self._kick.set()
        if item is not None:
            self._event(event, item, **numbers)
            stops = event in ("dismissed", "snoozed", "cancelled", "timeout")
            quiet = event == "snoozed" and not numbers.get("was_ringing", True)  # a missed one
            if stops and not quiet and self.hub:
                self.hub.stopped(item, numbers.get("how", event))
        self._broadcast()

    def note_error(self, reason: str, source: str) -> None:
        """A refused or unclear tool call: its reason label only."""
        self._write({"event": "tool_error", "source": source, "reason": reason})

    def _event(self, event: str, item: TimerItem, **numbers: Any) -> None:
        """Telemetry: labels and numbers only (never the label or reminder text)."""
        record: dict[str, Any] = {"event": event, "item_kind": item.kind, "source": item.created_by}
        for key, value in numbers.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                record[key] = round(float(value), 1)
            elif key in ("how", "reason"):
                record[key] = str(value)
        self._write(record)

    def _write(self, record: dict[str, Any]) -> None:
        if self.telemetry is None or not self.telemetry.enabled:
            return
        base = {"schema": 1, "kind": "timer", "started_at": iso_now(self.clock(), self.zone)}
        with contextlib.suppress(OSError):  # telemetry must never break a timer
            self.telemetry.write({**base, **record})
