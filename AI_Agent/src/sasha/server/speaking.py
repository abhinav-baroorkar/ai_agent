"""Brain-wide speaking state, so the listener's microphone never hears the assistant.

Half-duplex on one socket isn't enough: the window's Home socket speaks through the
same speakers while the listener's mic is open (design D2). So every conversation
reports its own `tts_start` / `tts_end`, and while any socket is speaking (plus a
short tail after the last `tts_end`) wake segments are dropped and every listener is
told `pause_capture`; `resume_capture` follows once it is quiet again.

Also tracks the end of the last turn on any socket, for the wake cool-down.
Event-loop only: all calls come from coroutines or loop callbacks.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Callable

Notify = Callable[[bool], None]  # True = pause capture, False = resume


class SpeakingTracker:
    def __init__(
        self, tail_s: float, cooldown_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.tail_s = tail_s
        self.cooldown_s = cooldown_s
        self.clock = clock
        self._speaking: set[int] = set()
        self._quiet_since = -math.inf
        self._turn_ended = -math.inf
        self._listeners: dict[int, tuple[asyncio.AbstractEventLoop, Notify]] = {}
        self._paused = False
        self._resume: asyncio.TimerHandle | None = None

    # --- speaking ---------------------------------------------------------------
    def speaking(self) -> bool:
        """True while any socket speaks, and for `tail_s` after the last one stops."""
        return bool(self._speaking) or self.clock() - self._quiet_since < self.tail_s

    def started(self, sid: int) -> None:
        self._speaking.add(sid)
        if self._resume is not None:
            self._resume.cancel()
            self._resume = None
        if not self._paused:
            self._paused = True
            self._notify(True)

    def ended(self, sid: int) -> None:
        if sid not in self._speaking:
            return
        self._speaking.discard(sid)
        if self._speaking:
            return
        self._quiet_since = self.clock()
        loop = asyncio.get_running_loop()
        if self._resume is not None:
            self._resume.cancel()
        self._resume = loop.call_later(self.tail_s, self._maybe_resume)

    def _maybe_resume(self) -> None:
        self._resume = None
        if self._paused and not self.speaking():
            self._paused = False
            self._notify(False)

    async def wait_quiet(self, timeout_s: float) -> bool:
        """Wait until nothing is speaking (tail included). False on timeout."""
        deadline = self.clock() + timeout_s
        while self.speaking():
            if self.clock() >= deadline:
                return False
            await asyncio.sleep(0.05)
        return True

    # --- turns (cool-down) -----------------------------------------------------
    def turn_ended(self) -> None:
        self._turn_ended = self.clock()

    def in_cooldown(self) -> bool:
        return self.clock() - self._turn_ended < self.cooldown_s

    # --- listeners ---------------------------------------------------------------
    def subscribe(self, sid: int, notify: Notify) -> None:
        """Call from the listener's own event loop; notices are delivered on that loop."""
        self._listeners[sid] = (asyncio.get_running_loop(), notify)
        if self._paused:
            notify(True)

    def unsubscribe(self, sid: int) -> None:
        self._listeners.pop(sid, None)
        self.ended(sid)

    def _notify(self, pause: bool) -> None:
        for loop, notify in list(self._listeners.values()):
            # thread-safe even if a socket runs on another loop (as in tests)
            loop.call_soon_threadsafe(notify, pause)


class ListenerPresence:
    """Is a wake listener connected and not paused? Drives Ollama's keep_alive.

    While one is listening, LLM requests ask Ollama to keep the model loaded
    (`keep_alive_while_listening`, -1 = forever), so it is never reloaded with
    speech-to-text already on the GPU (which can split it onto the CPU). Paused or
    gone: `keep_alive()` is None and requests use the profile's normal value again.
    Thread-safe reads (worker threads build the LLM requests).
    """

    def __init__(self, keep_alive_while_listening: int | str = -1) -> None:
        self.value = keep_alive_while_listening
        self._listening: set[int] = set()
        # Ollama keeps the last keep_alive it was sent, so when the last listener goes
        # it must be told the normal value again (S4). Set by deps; must not raise.
        self.on_idle: Callable[[], None] | None = None

    def set(self, sid: int, listening: bool) -> None:
        was_active = self.active
        if listening:
            self._listening.add(sid)
        else:
            self._listening.discard(sid)
        if was_active and not self.active and self.on_idle is not None:
            with contextlib.suppress(Exception):  # restoring must never break a socket
                self.on_idle()

    @property
    def active(self) -> bool:
        return bool(self._listening)

    def keep_alive(self) -> int | str | None:
        return self.value if self._listening else None
