"""The owner takes over the assistant's browser, first version (browser-design.md §9).

Modes: `assistant` (headless, normal), `waiting` (asked; a running turn is finishing),
`switching`, `owner` (his visible window is open). The flow:

1. `driving` is claimed at once, so no new browser step starts (every bot is told the
   owner is using it). A running turn may finish, bounded by its own limits; the owner
   can end it now (`stop_turn`, the existing owner stop).
2. The headless Chrome stops (the usual history wipe runs). The configuration files are
   copied aside (C1) and the take-over preferences seeded; a failure refuses.
3. A visible Chrome starts on the same marked profile, with no DevTools connection at
   all, no extensions or sync, through its own guard proxy, on the search engine's home
   page only (C2). The death-signal wrapper ends it with the brain.
4. Hand-back: the window closes (by him, the button or the time limit). The process must
   be fully gone (`wait_until_free`); then the configuration is restored, extensions
   removed, the take-over files and the usual history wiped. The next headless start
   keeps only the engine's cookies (C3). A failed start goes straight back (S2).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sasha.browser.chrome import find_chrome
from sasha.browser.history import forget_history
from sasha.browser.profile import prepare_profile, wait_until_free
from sasha.browser.proxy import FilteringProxy
from sasha.browser.redirects import site_of
from sasha.browser.service import BrowserService
from sasha.browser.takeover_files import (
    TakeoverRefused,
    clear_extensions,
    engine_home,
    restore_config,
    seed_takeover_preferences,
    snapshot_config,
    visible_flags,
    wipe_takeover_files,
)
from sasha.listener.capture import with_death_signal
from sasha.server.launch import browser_env

log = logging.getLogger(__name__)

MAX_OWNER_S = 900.0  # 15 minutes, then the server hands back (design 9.5)
WARN_BEFORE_S = 120.0  # the panel warns from 13 minutes
WAIT_FOR_TURN_S = 135.0  # a running turn's own limits end it before this
EXIT_WAIT_S = 3.0  # SIGTERM, then SIGKILL


class TakeOver:
    def __init__(
        self,
        service: BrowserService,
        search_template: Callable[[], str | None],
        spawn: Callable[..., Any] | None = None,
        has_display: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service = service
        self.search_template = search_template
        self.spawn = spawn or asyncio.create_subprocess_exec
        self.has_display = has_display or (
            lambda: bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        )
        self.clock = clock
        self.mode = "assistant"
        self.owner_until: datetime | None = None
        self.proxy: FilteringProxy | None = None
        self.process: Any = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def root(self) -> Path:
        return prepare_profile(self.service.data_dir)

    @property
    def snapshot(self) -> Path:
        return self.service.data_dir / "browser-takeover-config"

    def state(self) -> dict[str, Any]:
        until = self.owner_until.isoformat(timespec="seconds") if self.owner_until else None
        owner_refused = dict(self.proxy.stats.refused) if self.proxy is not None else {}
        return {"mode": self.mode, "owner_until": until, "owner_refused": owner_refused}

    def _set(self, mode: str) -> None:
        self.mode = mode
        self.service.changed()

    # --- starting -----------------------------------------------------------------------
    async def start(self) -> None:
        """Begin a take-over (returns at once; the switch runs in the background)."""
        if self.mode != "assistant":
            raise TakeoverRefused("The owner is already driving the browser")
        if not self.has_display():
            raise TakeoverRefused("No screen is available for the browser window")
        if find_chrome() is None:
            raise TakeoverRefused("Chrome or Chromium is not installed")
        self.service.driving = True  # from now on no browser step starts
        self._set("waiting")
        self._task = asyncio.get_running_loop().create_task(self._switch_to_owner())

    def stop_turn(self) -> None:
        """ "Stop it now": the running turn's next step fails (the existing owner stop)."""
        self.service.owner_stops += 1
        if self.service.turn_holder is not None:
            self.service.end_turn(self.service.turn_holder)

    async def _switch_to_owner(self) -> None:
        try:
            deadline = self.clock() + WAIT_FOR_TURN_S
            while (self.service.turn_holder or self.service.holder) and self.clock() < deadline:
                await asyncio.sleep(0.2)
            if self.service.turn_holder is not None:  # a hung turn: its limits failed
                self.stop_turn()
            self._set("switching")
            await self.service.stop("take_over")  # the usual wipe runs after Chrome exits
            root = self.root
            if not await wait_until_free(root):
                raise TakeoverRefused("The assistant's browser didn't close")
            snapshot_config(root, self.snapshot)  # C1
            seed_takeover_preferences(root)
            await self._launch(root)
        except Exception as exc:  # S2: anything failed: wipe and go straight back
            log.warning("Take-over didn't start (%s)", type(exc).__name__)
            await self._restore()
            return
        self.owner_until = datetime.now().astimezone() + timedelta(seconds=MAX_OWNER_S)
        self._set("owner")
        await self._watch()

    async def _launch(self, root: Path) -> None:
        exe = find_chrome()
        assert exe is not None
        self.proxy = self.service.proxy_factory()
        port = await self.proxy.start()
        home = engine_home(self.search_template())
        argv, hook = with_death_signal([exe, *visible_flags(root, port, home)])
        self.process = await self.spawn(
            *argv,
            env=browser_env(root),
            cwd=root,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            preexec_fn=hook,
        )

    # --- handing back -------------------------------------------------------------------
    async def _watch(self) -> None:
        """Until the window closes or the time is up."""
        ends = self.clock() + MAX_OWNER_S
        while self.process is not None and self.process.returncode is None:
            if self.clock() >= ends:
                log.info("Take-over time is up: handing back")
                break
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.process.wait(), 1.0)
        await self.hand_back()

    async def hand_back(self) -> None:
        async with self._lock:
            if self.mode == "assistant":
                return
            self._set("switching")
            await self._end_process()
            await self._restore()

    async def _end_process(self) -> None:
        proc, self.process = self.process, None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), EXIT_WAIT_S)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 5.0)

    async def _restore(self) -> None:
        """Back to the assistant's browser, with nothing lasting from the owner's window."""
        try:
            root = self.root
            if await wait_until_free(root):  # never touch the profile under a live Chrome
                if self.snapshot.exists():
                    restore_config(root, self.snapshot)
                clear_extensions(root)
                wipe_takeover_files(root)
                forget_history(root)
            else:
                log.warning("Take-over: the profile is still in use; cleaned at next start")
            # C3 at the next headless start: only the engine's cookies (none if no engine)
            host = urlsplit(self.search_template() or "").hostname or ""
            self.service.keep_cookies_of = site_of(host) if host else ""
        finally:
            if self.proxy is not None:
                await self.proxy.stop()
                self.proxy = None
            self.owner_until = None
            self.service.driving = False
            self._set("assistant")
