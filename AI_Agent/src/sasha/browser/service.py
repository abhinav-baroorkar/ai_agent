"""One browser for the brain: started on first use, stopped when idle, one bot at a time.

The brain owns a single `BrowserService`. A conversation's turn holds it from its first
step (`acquire(turn=True)`) until the turn ends; another conversation is refused
meanwhile, and gets it afterwards without the previous holder's tabs (cookies are
shared: one profile in phase 1). Chrome and the filtering proxy are started lazily and
stopped after `idle_s` without use, or when the brain stops. Each stop forgets the
browsing history (`history.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from sasha.browser.cdp import CDPError
from sasha.browser.chrome import BrowserUnavailableError, Chrome, start_chrome
from sasha.browser.cookies import keep_only_site_data
from sasha.browser.history import forget_when_free
from sasha.browser.profile import profile_dir
from sasha.browser.proxy import FilteringProxy
from sasha.browser.takeover_cleanup import finish_takeover, pending
from sasha.browser.takeover_files import extensions_present

log = logging.getLogger(__name__)

IDLE_S = 600.0
# A turn's browser budget is 90 s, and one step can run up to 40 s past it (tools.py
# SECONDS_PER_TURN + OP_TIMEOUT_S): past that the hold is released whatever the turn does.
MAX_TURN_HOLD_S = 130.0


BUSY = "The browser is in use by another bot; try again shortly"
DRIVING = (
    "The owner is using the browser right now, or it is being cleaned up after; "
    "try again after it's handed back"
)


class BrowserBusyError(RuntimeError):
    """Another bot is using the browser right now."""


class BrowserStoppedError(RuntimeError):
    """The owner stopped the browser during this turn."""


Starter = Callable[[Path, int], Awaitable[Chrome]]


class BrowserService:
    def __init__(
        self,
        data_dir: Path,
        idle_s: float = IDLE_S,
        starter: Starter = start_chrome,
        proxy_factory: Callable[[], FilteringProxy] = FilteringProxy,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.data_dir = data_dir
        self.idle_s = idle_s
        self.starter = starter
        self.proxy_factory = proxy_factory
        self.clock = clock
        self.chrome: Chrome | None = None
        self.proxy: FilteringProxy | None = None
        self.holder: str | None = None
        self._last_used = clock()
        self._start_lock = asyncio.Lock()
        self._idle_task: asyncio.Task[None] | None = None
        self.last_stop: str | None = None  # "idle", "owner", "brain_stopping" (live view)
        self.owner_stops = 0  # a turn that saw a different count was stopped by the owner
        self.turn_holder: str | None = None  # the conversation whose turn uses the browser
        self.last_turn_holder: str | None = None  # whose tabs and cookies Chrome has now
        self.holder_epoch = 0  # +1 at every hand-over to another holder
        self._claims = 0  # +1 per turn claim, so a stale backstop can't end a newer one
        self._hold_timer: asyncio.TimerHandle | None = None
        self.listeners: list[Callable[[], None]] = []  # told of every state change
        self.driving = False  # the owner's take-over is on (claimed before it starts)
        self.keep_cookies_of: str | None = None  # after a take-over: only this site's (C3)

    def changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    def end_turn(self, holder: str) -> None:
        """The holder's turn ended (its turn-end hook, also on error or cancel) or it hit
        its step or time limit: another conversation may take the browser now."""
        if self.turn_holder == holder:
            self.turn_holder = None
            if self._hold_timer is not None:
                self._hold_timer.cancel()
                self._hold_timer = None
            self.touch()
            self.changed()

    def _claim_turn(self, holder: str) -> None:
        """A new turn holds the browser. A backstop releases it after MAX_TURN_HOLD_S even
        if the turn hangs (in the LLM, in a long reply) and never ends (review S1)."""
        if self.turn_holder == holder:
            return
        self.turn_holder = holder
        if self._hold_timer is not None:
            self._hold_timer.cancel()
        self._claims += 1
        claim = self._claims
        self._hold_timer = asyncio.get_running_loop().call_later(
            MAX_TURN_HOLD_S, self._release_stale, claim
        )

    def _release_stale(self, claim: int) -> None:
        if claim == self._claims and self.turn_holder is not None:
            log.warning("A browser turn held it past %.0f s: released", MAX_TURN_HOLD_S)
            self._hold_timer = None
            self.end_turn(self.turn_holder)

    @contextlib.asynccontextmanager
    async def acquire(
        self, bot_id: str, owner_stops: int | None = None, turn: bool = False
    ) -> AsyncIterator[Chrome]:
        """One step of `bot_id` (a conversation's holder id). `owner_stops`: the count
        the caller's turn began with; if the owner stopped the browser since, refuse, so
        a stop just before this step can't be undone by the step starting Chrome again.
        `turn`: the step belongs to a turn, which holds the browser until `end_turn`:
        while another turn holds it, refuse (review W1). The checks and the claim run
        here, on the loop, with no await in between, so two first steps can't both pass.
        A new holder gets the browser without the previous holder's tabs."""
        if owner_stops is not None and owner_stops != self.owner_stops:
            raise BrowserStoppedError("The owner stopped the browser")
        if self.driving:  # the owner's take-over: every bot's step waits for hand-back
            raise BrowserBusyError(DRIVING)
        busy = self.holder not in (None, bot_id) or (
            turn and self.turn_holder not in (None, bot_id)
        )
        if busy:
            raise BrowserBusyError(BUSY)
        self.holder = bot_id
        if turn:
            self._claim_turn(bot_id)
        self.changed()
        try:
            chrome = await self._ensure_started()
            if turn and self.last_turn_holder != bot_id:
                if self.last_turn_holder is not None:
                    await self._hand_over(chrome)
                self.last_turn_holder = bot_id
            yield chrome
        finally:
            self.holder = None
            self.touch()
            self.changed()

    async def _hand_over(self, chrome: Chrome) -> None:
        """Close the previous holder's tabs before a new holder starts, so no conversation
        sees another's pages; the epoch tells the previous holder's page layer that its
        tabs are gone. Cookies are kept on purpose: one profile for the brain (a bot
        challenge the owner passed must persist); separate profiles come with sign-ins."""
        for target in list(chrome.tab_targets):
            with contextlib.suppress(CDPError):
                await chrome.pipe.call("Target.closeTarget", {"targetId": target}, timeout=3)
        chrome.tab_targets, chrome.current_target = [], None
        self.holder_epoch += 1

    def touch(self) -> None:
        self._last_used = self.clock()

    async def _ensure_started(self) -> Chrome:
        async with self._start_lock:
            if self.chrome is None or self.chrome.process.returncode is not None:
                self.proxy = self.proxy_factory()
                port = await self.proxy.start()
                try:
                    if pending(self.data_dir):  # K2: a take-over the brain didn't finish
                        try:
                            self.keep_cookies_of = await finish_takeover(self.data_dir)
                        except Exception as exc:
                            raise BrowserUnavailableError(str(exc)) from None
                    if extensions_present(profile_dir(self.data_dir)):  # C1: never run them
                        raise BrowserUnavailableError(
                            "Extensions appeared in the browser profile; it won't start"
                        )
                    self.chrome = await self.starter(self.data_dir, port)
                    if self.keep_cookies_of is not None:  # C3, before the first navigation
                        await keep_only_site_data(self.chrome, self.keep_cookies_of)
                        self.keep_cookies_of = None
                except Exception:
                    if self.chrome is not None:
                        with contextlib.suppress(Exception):
                            await self.chrome.stop()
                        self.chrome = None
                    await self.proxy.stop()
                    self.proxy = None
                    raise
                if self._idle_task is None or self._idle_task.done():
                    self._idle_task = asyncio.create_task(self._idle_watch())
            self.touch()
            return self.chrome

    async def _idle_watch(self) -> None:
        while self.chrome is not None:
            await asyncio.sleep(min(30.0, max(self.idle_s / 4, 0.01)))
            busy = self.holder is not None or self.turn_holder is not None
            if not busy and self.clock() - self._last_used >= self.idle_s:
                log.info("Browser idle: stopping it")
                self.last_stop = "idle"
                await self._shutdown()
                return

    async def _shutdown(self) -> None:
        chrome, proxy = self.chrome, self.proxy
        self.chrome = self.proxy = None
        self.changed()
        if chrome is not None:
            await chrome.stop()
            await forget_when_free(profile_dir(self.data_dir))  # once Chrome has exited
        if proxy is not None:
            await proxy.stop()

    async def stop(self, reason: str = "brain_stopping") -> None:
        if reason == "owner":
            self.owner_stops += 1
        if self.chrome is not None:
            self.last_stop = reason
        if self._idle_task is not None:
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
        await self._shutdown()

    def stats(self) -> dict[str, Any]:
        """Counts only, for telemetry."""
        if self.proxy is None:
            return {"running": False}
        s = self.proxy.stats
        return {
            "running": True,
            "allowed": s.allowed,
            "refused": dict(s.refused),
            "background_refused": s.background_refused,
        }
