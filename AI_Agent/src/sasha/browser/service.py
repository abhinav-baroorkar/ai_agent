"""One browser for the brain: started on first use, stopped when idle, one bot at a time.

The brain owns a single `BrowserService`. A bot `acquire()`s it for a turn; a second
bot is refused while the first holds it (no shared tabs or cookies between bots in
phase 1). Chrome and the filtering proxy are started lazily and stopped after
`idle_s` without use, or when the brain stops. Each stop forgets the browsing history
(`history.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from sasha.browser.chrome import Chrome, start_chrome
from sasha.browser.history import forget_when_free
from sasha.browser.profile import profile_dir
from sasha.browser.proxy import FilteringProxy

log = logging.getLogger(__name__)

IDLE_S = 600.0


class BrowserBusyError(RuntimeError):
    """Another bot is using the browser right now."""


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

    @contextlib.asynccontextmanager
    async def acquire(self, bot_id: str) -> AsyncIterator[Chrome]:
        if self.holder is not None and self.holder != bot_id:
            raise BrowserBusyError("The browser is in use by another bot")
        self.holder = bot_id
        try:
            yield await self._ensure_started()
        finally:
            self.holder = None
            self.touch()

    def touch(self) -> None:
        self._last_used = self.clock()

    async def _ensure_started(self) -> Chrome:
        async with self._start_lock:
            if self.chrome is None or self.chrome.process.returncode is not None:
                self.proxy = self.proxy_factory()
                port = await self.proxy.start()
                try:
                    self.chrome = await self.starter(self.data_dir, port)
                except Exception:
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
            if self.holder is None and self.clock() - self._last_used >= self.idle_s:
                log.info("Browser idle: stopping it")
                self.last_stop = "idle"
                await self._shutdown()
                return

    async def _shutdown(self) -> None:
        chrome, proxy = self.chrome, self.proxy
        self.chrome = self.proxy = None
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
