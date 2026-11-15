"""Fail-closed guards: capture is allowed only while every check says yes.

Checks (each proven by a direct call, re-read every 2 s, and also pushed by signals):
- the screen is not locked (`org.gnome.ScreenSaver.GetActive`);
- the logind session is active (no user switch);
- the system is not preparing to sleep (logind `PrepareForSleep`, with a delay
  inhibitor taken at start so capture is stopped before the inhibitor is released);
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
    """A pushed change: `kind` is "lock", "active", "battery" or "sleep"."""

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
        """Signals; raises if a subscription could not be made."""
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

    def allowed(self) -> tuple[bool, str]:
        s = self.state
        checks = [
            (s.connected, "system bus not connected"),
            (s.subscribed, "signal subscription failed"),
            (s.error is None, f"guard read failed ({s.error})"),
            (s.locked is False, "screen locked or lock state unknown"),
            (s.active is True, "session not active or unknown"),
            (not s.sleeping, "system going to sleep"),
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
            self.backend.take_sleep_inhibitor()
        except Exception as e:
            self._fail(f"connect: {type(e).__name__}")
            return
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
        with contextlib.suppress(Exception):
            self.backend.release_sleep_inhibitor()
        with contextlib.suppress(Exception):
            await self.backend.close()

    def _fail(self, reason: str) -> None:
        log.warning("Listener guard failed (%s): microphone stays closed", reason)
        self.state.error = reason
        self.on_change()

    async def poll_once(self) -> None:
        """Prove each fact with a direct call. Any error closes the microphone."""
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
            events = self.backend.events()
            self.state.subscribed = True
            self.on_change()
            async for event in events:
                self.handle(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.state.subscribed = False
            self._fail(f"signals: {type(e).__name__}")

    def handle(self, event: GuardEvent) -> None:
        s = self.state
        if event.kind == "lock":
            s.locked = event.value
        elif event.kind == "active":
            s.active = event.value
        elif event.kind == "battery":
            s.battery = event.value
        elif event.kind == "sleep":
            self._sleep(event.value)
            return
        self.on_change()

    def _sleep(self, going: bool) -> None:
        s = self.state
        if going:
            s.sleeping = True
            self.on_change()  # the listener kills the capture child here, synchronously
            with contextlib.suppress(Exception):
                self.backend.release_sleep_inhibitor()  # only after the mic is closed
        else:
            s.sleeping = False
            try:
                self.backend.take_sleep_inhibitor()
            except Exception as e:
                self._fail(f"inhibitor: {type(e).__name__}")
                return
            self.on_change()
