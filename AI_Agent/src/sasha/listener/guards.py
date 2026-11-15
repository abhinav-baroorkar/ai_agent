"""Fail-closed guards: capture is allowed only while every check says yes.

Checks (each proven by a direct call, re-read every 2 s, and also pushed by signals):
- the screen is not locked (`org.gnome.ScreenSaver.GetActive`);
- the logind session is active (no user switch);
- the system is not preparing to sleep (logind `PrepareForSleep`), and a logind delay
  inhibitor is really held, so capture is always stopped before sleep can proceed
  (if taking it fails, capture stays off and it is retried on every poll);
- not on battery, when "pause on battery" is on (UPower `OnBattery`).

Any failed call, failed subscription or unreadable answer counts as "no". The guard
never assumes "unlocked" or "active".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

POLL_S = 2.0


@dataclass(frozen=True)
class GuardEvent:
    """A pushed change: `kind` is "lock", "active", "battery" or "sleep"; the backend's
    first event is "ready", sent only after every subscription was made."""

    kind: str
    value: bool


class GuardBackend(Protocol):
    """System facts. Real: `dbus_backend.DBusBackend`; tests use a fake."""

    async def connect(self) -> None: ...

    async def screen_locked(self) -> bool: ...

    async def session_active(self) -> bool: ...

    async def on_battery(self) -> bool: ...

    def take_sleep_inhibitor(self) -> None: ...

    def release_sleep_inhibitor(self) -> None: ...

    def events(self) -> AsyncIterator[GuardEvent]:
        """Signals: first `ready` once all subscriptions exist; raises on failure."""
        ...

    async def close(self) -> None: ...


@dataclass
class GuardState:
    connected: bool = False
    subscribed: bool = False
    locked: bool | None = None  # None: unknown, which counts as "no"
    active: bool | None = None
    battery: bool | None = None
    sleeping: bool = False
    inhibited: bool = False  # True only while a sleep inhibitor is really held
    error: str | None = None


class Guards:
    """Owns the backend; calls `on_change()` (synchronously) whenever the answer may
    have changed. `allowed()` is cheap and safe to call every chunk."""

    def __init__(
        self,
        backend: GuardBackend,
        on_change: Callable[[], None],
        pause_on_battery: bool = True,
        poll_s: float = POLL_S,
    ) -> None:
        self.backend = backend
        self.on_change = on_change
        self.pause_on_battery = pause_on_battery
        self.poll_s = poll_s
        self.state = GuardState()
        self._tasks: list[asyncio.Task[None]] = []
        self._inhibit_warned = False

    def allowed(self) -> tuple[bool, str]:
        s = self.state
        checks = [
            (s.connected, "system bus not connected"),
            (s.subscribed, "signal subscription failed"),
            (s.error is None, f"guard read failed ({s.error})"),
            (s.locked is False, "screen locked or lock state unknown"),
            (s.active is True, "session not active or unknown"),
            (not s.sleeping, "system going to sleep"),
            (s.inhibited, "no sleep inhibitor held"),
        ]
        if self.pause_on_battery:
            checks.append((s.battery is False, "on battery or battery state unknown"))
        for ok, reason in checks:
            if not ok:
                return False, reason
        return True, "ok"

    async def start(self) -> None:
        try:
            await self.backend.connect()
            self.state.connected = True
        except Exception as e:
            self._fail(f"connect: {type(e).__name__}")
            return
        await self._take_inhibitor()
        await self.poll_once()
        self._tasks = [
            asyncio.create_task(self._watch_signals()),
            asyncio.create_task(self._poll_forever()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._release_inhibitor()
        with contextlib.suppress(Exception):
            await self.backend.close()

    async def _take_inhibitor(self) -> None:
        """Blocking D-Bus call (fd passing), so it runs in a worker thread."""
        try:
            await asyncio.to_thread(self.backend.take_sleep_inhibitor)
        except Exception as e:
            if not self._inhibit_warned:
                log.warning("No sleep inhibitor (%s): microphone stays closed", type(e).__name__)
            self._inhibit_warned = True
            self.state.inhibited = False
        else:
            self._inhibit_warned = False
            self.state.inhibited = True
        self.on_change()

    def _release_inhibitor(self) -> None:
        self.state.inhibited = False
        with contextlib.suppress(Exception):
            self.backend.release_sleep_inhibitor()

    def _fail(self, reason: str) -> None:
        log.warning("Listener guard failed (%s): microphone stays closed", reason)
        self.state.error = reason
        self.on_change()

    async def poll_once(self) -> None:
        """Prove each fact with a direct call. Any error closes the microphone."""
        if not self.state.inhibited and not self.state.sleeping and self.state.connected:
            await self._take_inhibitor()  # retried until it works
        try:
            locked = await self.backend.screen_locked()
            active = await self.backend.session_active()
            battery = await self.backend.on_battery() if self.pause_on_battery else False
        except Exception as e:
            self.state.locked = self.state.active = self.state.battery = None
            self._fail(f"read: {type(e).__name__}")
            return
        changed = (locked, active, battery) != (
            self.state.locked,
            self.state.active,
            self.state.battery,
        )
        self.state.locked, self.state.active, self.state.battery = locked, active, battery
        if self.state.error is not None or changed:
            self.state.error = None
            self.on_change()

    async def _poll_forever(self) -> None:
        while True:
            await asyncio.sleep(self.poll_s)
            await self.poll_once()

    async def _watch_signals(self) -> None:
        try:
            async for event in self.backend.events():
                if event.kind == "ready":
                    self.state.subscribed = True  # only now do all subscriptions exist
                    self.on_change()
                else:
                    await self.handle(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.state.subscribed = False
            self._fail(f"signals: {type(e).__name__}")

    async def handle(self, event: GuardEvent) -> None:
        s = self.state
        if event.kind == "lock":
            s.locked = event.value
        elif event.kind == "active":
            s.active = event.value
        elif event.kind == "battery":
            s.battery = event.value
        elif event.kind == "sleep":
            await self._sleep(event.value)
            return
        self.on_change()

    async def _sleep(self, going: bool) -> None:
        s = self.state
        if going:
            s.sleeping = True
            self.on_change()  # the listener kills the capture child here, synchronously
            self._release_inhibitor()  # only after the mic is closed
        else:
            s.sleeping = False
            await self._take_inhibitor()  # allowed() stays False until it is held again
