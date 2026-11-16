"""A conversation socket's timer side (mixed into server/ws.py's Conversation, which is
near the 400-line limit): what the hub calls from any thread, the `timer_dismiss` /
`timer_snooze` frames, and the typed-path check for claimed timer changes."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sasha.browser.helper import BROWSE_TOOL
from sasha.memory.check import owner_pair
from sasha.memory.tools import MEMORY_TOOLS
from sasha.memory.truth import MemoryTruth
from sasha.prompting import prompt_lines
from sasha.server.browse_truth import BrowseTruth
from sasha.server.timers.service import TimerError
from sasha.server.timers.status import kinds_asked, status_line
from sasha.server.timers.tools import TIMER_TOOLS
from sasha.server.timers.truth import TimerTruth, asks_for_timer
from sasha.server.turn_check import ChainCheck, TurnCheck

if TYPE_CHECKING:
    from sasha.orchestrator import Assistant
    from sasha.server.timers.hub import Hub
    from sasha.server.timers.ring import RingPlayer


class TimerSocket:
    # Provided by the Conversation this is mixed into.
    timers: Hub | None
    ringer: RingPlayer | None
    loop: asyncio.AbstractEventLoop
    assistant: Assistant
    speak: bool
    timer_claims: bool  # check replies for claimed timer changes (False on the listener)
    turn_replaced: bool = False  # the turn's own check replaced the reply (a secret)
    emit_threadsafe: Callable[[Any], None]
    error: Callable[[str], None]

    # --- the hub calls these from any thread ---------------------------------------
    def push_threadsafe(self, frame: dict[str, Any]) -> None:
        self.emit_threadsafe(frame)

    def ring_threadsafe(self, item_id: str, say: str, tone: bool, check: bool = True) -> None:
        if self.ringer is not None:
            self.ringer.ring_threadsafe(item_id, say, tone, check)

    def stop_ring_threadsafe(self, item_id: str) -> None:
        with contextlib.suppress(RuntimeError):  # the loop is gone: nothing to stop
            self.loop.call_soon_threadsafe(self.ring_stopped, item_id)

    def ring_stopped(self, item_id: str) -> None:
        """The item ended: its ring speech stops (the listener also closes its window)."""
        if self.ringer is not None:
            self.ringer.stop(item_id)

    def ring_started(self, item_id: str) -> None:
        """A ring's speech is about to start on this socket (the listener overrides)."""

    async def ring_played(self, item_id: str) -> None:
        """A ring's speech was sent (the listener opens its stop window)."""

    # --- frames and replies ------------------------------------------------------------
    async def _on_timer_frame(self, kind: str, msg: dict[str, Any]) -> None:
        if self.timers is None:
            self.error("Timers are not available")
            return
        service, item_id = self.timers.service, msg.get("id")
        minutes = msg.get("minutes", service.config.snooze_minutes)
        if not isinstance(item_id, str) or (
            kind == "timer_snooze" and (isinstance(minutes, bool) or not isinstance(minutes, int))
        ):
            self.error("Bad timer message")
            return
        try:
            if kind == "timer_dismiss":
                await asyncio.to_thread(service.dismiss, item_id)
            else:
                await asyncio.to_thread(service.snooze, item_id, minutes)
        except TimerError as exc:
            self.error(str(exc))

    def _timer_truth(self, command: str) -> TimerTruth | None:
        """Typed and window turns of a bot with timer tools (wake turns have their own
        truthful reply, so the listener turns this off)."""
        names = self.assistant.tools.names
        if not self.timer_claims or self.timers is None or not set(TIMER_TOOLS) & set(names):
            return None
        cfg, assistant = self.timers.config, self.assistant
        # An answer to a timer question ("Morning.") is part of that request (the
        # said-check's span-2 rule): held on a spoken turn and judged like the request.
        previous, since = assistant.previous_request(), assistant.since_previous()
        answering = (
            previous is not None
            and assistant.previous_reply_asked()
            and asks_for_timer(previous, cfg)
            and (since is None or since <= cfg.answer_window_s)
        )
        service = self.timers.service
        return TimerTruth(
            cfg,
            command,
            spoken=self.speak,
            answering=answering,
            state=lambda words: status_line(service, kinds_asked(words)),
        )

    def _turn_check(self, command: str) -> ChainCheck | None:
        """The turn's checks: the timers' (above) and memory's (memory/truth.py), held
        and finished together (server/turn_check.py). None when neither applies."""
        checks: list[TurnCheck] = []
        if (timer := self._timer_truth(command)) is not None:
            checks.append(timer)
        assistant = self.assistant
        if set(MEMORY_TOOLS) & set(assistant.tools.names):
            cfg = assistant.settings.profile.memory
            previous = assistant.previous_request()
            said = (previous, command) if previous is not None else (command,)
            asked, since = assistant.previous_reply_asked(), assistant.since_previous()
            pair = owner_pair(said, asked, since, cfg)  # the same words as the tools (R2)
            # Wake turns (the listener) keep their own truthful reply; only the secret
            # rule runs there, so a refused secret stays out of the history (R3).
            wake = not self.timer_claims
            redact = assistant.redact_request
            checks.append(MemoryTruth(cfg, pair, self.speak, redact, wake=wake))
        if BROWSE_TOOL in assistant.tools:  # a lookup that brought nothing back says so
            checks.append(BrowseTruth(prompt_lines(assistant.settings), command))
        return ChainCheck(checks) if checks else None
