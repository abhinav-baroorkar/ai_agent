"""The spoken-confirmation window (contract v0.2 addition: `confirm` / `confirm_result`).

After `{type:"confirm", action, timeout_s}` the brain speaks the question. Once that
speech has finished playing (plus the half-duplex gap), the listener opens the
microphone for exactly one segment, shows `listening`, and sends it as a normal wake
segment. The window closes when that segment is sent, on `confirm_result`, or
`timeout_s` after the end of the question's playback. If no question is played at all
(the brain has no speech), the window opens `NO_SPEECH_GRACE_S` after `confirm`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

NO_SPEECH_GRACE_S = 1.5
DEFAULT_TIMEOUT_S = 5.0


class ConfirmWindow:
    """States: idle -> pending (question announced) -> open (mic for one segment) -> idle."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self.on_change = on_change
        self.state = "idle"
        self.timeout_s = DEFAULT_TIMEOUT_S
        self._timer: asyncio.Task[None] | None = None

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    @property
    def pending(self) -> bool:
        return self.state == "pending"

    def announce(self, timeout_s: float, speech_follows: Callable[[], bool]) -> None:
        """`confirm` arrived. The window opens after the question has been played."""
        self.timeout_s = max(0.5, min(float(timeout_s or DEFAULT_TIMEOUT_S), 30.0))
        self.state = "pending"
        self._restart_timer(self._open_if_silent(speech_follows))

    async def _open_if_silent(self, speech_follows: Callable[[], bool]) -> None:
        await asyncio.sleep(NO_SPEECH_GRACE_S)
        if self.pending and not speech_follows():
            self.open()

    def speech_done(self) -> None:
        """The question's playback (and the half-duplex gap) is over."""
        if self.pending:
            self.open()

    def open(self) -> None:
        self.state = "open"
        self._restart_timer(self._expire())
        self.on_change()

    async def _expire(self) -> None:
        await asyncio.sleep(self.timeout_s)
        if self.is_open:
            log.info("Confirmation window ended without an answer")
            self.close()

    def close(self) -> None:
        if self.state == "idle":
            return
        self.state = "idle"
        if self._timer is not None and self._timer is not asyncio.current_task():
            self._timer.cancel()
        self._timer = None
        self.on_change()

    def _restart_timer(self, coro: object) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]

    async def aclose(self) -> None:
        timer, self._timer = self._timer, None
        self.state = "idle"
        if timer is not None:
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer
