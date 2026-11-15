"""Where a timer is heard: the connected sockets, and which one speaks a ring.

Every socket gets the `timers` snapshot, `timer_ringing` and `timer_stopped`.
Exactly one speaks: the Home socket (`speak=1`) if open, else the listener, else
none (the item stays `pending`). Late and pending items get only a generic line
(D2); their text is spoken only when the owner asks (`say`). Wake-made items say so
("Voice reminder: …", D4). Everything here is called under the service's lock, from
any thread; sockets are reached through their thread-safe methods.
"""

from __future__ import annotations

import math
import struct
import threading
from datetime import datetime
from typing import Any, Protocol

from sasha.config_timers import TimersConfig
from sasha.server.timers.model import TimerItem

TONE_RATE = 24000


class RingTarget(Protocol):
    ring_role: str  # "home" (speak=1 window), "listener", or "tab" (silent)

    def push_threadsafe(self, frame: dict[str, Any]) -> None: ...

    def ring_threadsafe(self, item_id: str, say: str, tone: bool) -> None: ...


def tone_pcm(rate: int = TONE_RATE, seconds: float = 0.35, freq: float = 880.0) -> bytes:
    """A short soft chime (s16le mono) with a fade in and out, no clicks."""
    n = int(rate * seconds)
    fade = int(rate * 0.03)
    out = bytearray()
    for i in range(n):
        gain = min(1.0, i / fade, (n - i) / fade) * 0.3
        out += struct.pack("<h", int(32767 * gain * math.sin(2 * math.pi * freq * i / rate)))
    return bytes(out)


TONE = tone_pcm()


class Hub:
    def __init__(self, config: TimersConfig) -> None:
        self.config = config
        self.service: Any = None  # TimerService, set by the app
        self._targets: list[RingTarget] = []
        self._device: dict[str, RingTarget] = {}  # item id -> the socket it rings on
        self._lock = threading.RLock()

    # --- sockets ------------------------------------------------------------------
    def add(self, target: RingTarget) -> None:
        with self._lock:
            self._targets.append(target)
        if self.service is None:
            return
        target.push_threadsafe({"type": "timers", **self.service.snapshot()})
        missed = self.service.deliver_pending()
        if missed:
            self._announce_missed(missed)

    def remove(self, target: RingTarget) -> None:
        with self._lock:
            if target in self._targets:
                self._targets.remove(target)
            for item_id, device in list(self._device.items()):
                if device is target:
                    del self._device[item_id]

    def _speaker(self) -> RingTarget | None:
        with self._lock:
            for role in ("home", "listener"):
                for target in self._targets:
                    if target.ring_role == role:
                        return target
        return None

    def _all(self, frame: dict[str, Any]) -> bool:
        with self._lock:
            targets = list(self._targets)
        for target in targets:
            target.push_threadsafe(frame)
        return bool(targets)

    # --- events from the service ----------------------------------------------------
    def broadcast(self, snapshot: dict[str, Any]) -> None:
        self._all({"type": "timers", **snapshot})

    def ring(self, item: TimerItem) -> bool:
        """Start a ring. False if no socket is connected (the item waits as pending)."""
        late = item.late_s > self.config.late_after_s
        say = self.generic([item]) if late else self.line(item)  # D2: late = generic only
        frame = {"type": "timer_ringing", "id": item.id, "kind": item.kind,
                 "created_by": item.created_by, "say": say}  # fmt: skip
        if not self._all(frame):
            return False
        speaker = self._speaker()
        if speaker is not None:
            with self._lock:
                self._device[item.id] = speaker
            speaker.ring_threadsafe(item.id, say, False)
        return True

    def tone(self, item: TimerItem) -> None:
        with self._lock:
            device = self._device.get(item.id)
        if device is None and (device := self._speaker()) is not None:
            with self._lock:
                self._device[item.id] = device
        if device is not None:
            device.ring_threadsafe(item.id, "", True)

    def stopped(self, item: TimerItem, how: str) -> None:
        with self._lock:
            self._device.pop(item.id, None)
        self._all({"type": "timer_stopped", "id": item.id, "how": how})

    def rings_on(self, item_id: str, target: RingTarget) -> bool:
        with self._lock:
            return self._device.get(item_id) is target

    # --- what is said -------------------------------------------------------------
    def line(self, item: TimerItem) -> str:
        """The announcement for an item ringing now (plain text)."""
        cfg = self.config
        if item.kind == "timer":
            text = cfg.timer_done_label.format(label=item.label) if item.label else cfg.timer_done
        elif item.kind == "alarm":
            assert self.service is not None
            when = datetime.fromtimestamp(item.due, self.service.zone).strftime("%H:%M")
            text = cfg.alarm_now.format(time=when) + (f" {item.label}." if item.label else "")
        else:
            text = cfg.reminder_now.format(text=item.text)
        if item.created_by == "wake":
            text = cfg.voice_prefix + text  # D4: voice-made items say so
        return text

    def generic(self, items: list[TimerItem]) -> str:
        """D2: "You have 2 missed reminders." Never a label or text."""
        kinds = {i.kind for i in items}
        if len(items) == 1:
            things = items[0].kind
        else:
            things = f"{items[0].kind}s" if len(kinds) == 1 else "items"
        return self.config.missed_line.format(count=len(items), things=things)

    def _announce_missed(self, items: list[TimerItem]) -> None:
        """Pending items found on connect: a generic line only, never their text."""
        say = self.generic(items)
        for item in items:
            self._all({"type": "timer_ringing", "id": item.id, "kind": item.kind,
                       "created_by": item.created_by, "say": say})  # fmt: skip
        speaker = self._speaker()
        if speaker is not None:
            speaker.ring_threadsafe(items[0].id, say, False)

    def say_text(self, item: TimerItem) -> bool:
        """The owner tapped an item (or asked): speak its own text now."""
        speaker = self._speaker()
        if speaker is None:
            return False
        speaker.ring_threadsafe(item.id, self.line(item), False)
        return True
