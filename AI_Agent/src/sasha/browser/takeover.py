"""The owner takes over the assistant's browser, first version (browser-design.md §9).

Modes: `assistant` (headless, normal), `waiting` (asked; a running turn is finishing),
`switching`, `owner` (his visible window is open). The flow:

1. `driving` is claimed at once, so no new browser step starts (every bot is told the
   owner is using it). A running turn may finish, bounded by its own limits; the owner
   can end it now (`stop_turn`, the existing owner stop).
2. The headless Chrome stops (the usual history wipe runs). The configuration files are
   copied aside (C1) and the take-over preferences seeded; a failure refuses.
3. A visible Chrome starts on the same marked profile, with no DevTools connection at
   all, no extensions or sync, through its own guard proxy. It opens the search that
   met a person check, through a private local page so no query is on its command line,
   or else the engine's home page (C2). The death-signal wrapper ends it with the brain.
4. Hand-back: the window closes (by him, the button or the time limit). The process must
   be fully gone (`wait_until_free`); then the configuration is restored, extensions
   removed, the take-over files, the sites' storage (K1) and the usual history wiped.
   The next headless start keeps only the engine's cookies (C3). A failed start goes
   straight back (S2).
- A marker written before the switch and removed only after a full restore lets the
  next headless start finish the job after a crash (K2, takeover_cleanup.py).
- A restore that fails stays in `restoring`: bots stay refused, and it retries (K3).
- Hand-back during the switch cancels the switch first; both run under one lock (K4).
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
from sasha.browser.profile import prepare_profile, wait_until_free
from sasha.browser.proxy import FilteringProxy
from sasha.browser.redirects import site_of
from sasha.browser.service import BrowserService
from sasha.browser.takeover_cleanup import (
    RestoreIncomplete,
    finish_takeover,
    pending,
    snapshot_path,
    write_marker,
)
from sasha.browser.takeover_files import (
    TakeoverRefused,
    seed_takeover_preferences,
    snapshot_config,
    start_page,
    visible_flags,
)
from sasha.listener.capture import with_death_signal
from sasha.server.launch import browser_env

log = logging.getLogger(__name__)

MAX_OWNER_S = 900.0  # 15 minutes, then the server hands back (design 9.5)
WARN_BEFORE_S = 120.0  # the panel warns from 13 minutes
WAIT_FOR_TURN_S = 135.0  # a running turn's own limits end it before this
EXIT_WAIT_S = 3.0  # SIGTERM, then SIGKILL
RETRY_S = 5.0  # a restore that failed is tried again this often (K3)
SAY_WHY_AFTER = 3  # failed restores before the panel says why


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
        self._retry: asyncio.Task[None] | None = None
        self._launching: asyncio.Future[None] | None = None
        self.restore_failures = 0
        self.restore_problem: str | None = None  # after SAY_WHY_AFTER failures
        self._lock = asyncio.Lock()

    @property
    def root(self) -> Path:
        return prepare_profile(self.service.data_dir)

    @property
    def snapshot(self) -> Path:
        return snapshot_path(self.service.data_dir)

    def engine_site(self) -> str:
        host = urlsplit(self.search_template() or "").hostname or ""
        return site_of(host) if host else ""

    def state(self) -> dict[str, Any]:
        until = self.owner_until.isoformat(timespec="seconds") if self.owner_until else None
        owner_refused = dict(self.proxy.stats.refused) if self.proxy is not None else {}
        return {
            "mode": self.mode,
            "owner_until": until,
            "owner_refused": owner_refused,
            "restore_problem": self.restore_problem,
            # a search met a person check and waits for him (yes or no, never the query):
            # the panel keeps Take over in view even when the browser has stopped
            "person_check": self.service.blocked_search() is not None,
        }

    def _set(self, mode: str) -> None:
        self.mode = mode
        self.service.changed()

    # --- starting -----------------------------------------------------------------------
    async def start(self) -> None:
        """Begin a take-over (returns at once; the switch runs in the background)."""
        if self.mode == "restoring":
            raise TakeoverRefused("The browser is still being cleaned up after the last take-over")
        if self.mode != "assistant":
            raise TakeoverRefused("The owner is already driving the browser")
        if not self.has_display():
            raise TakeoverRefused("No screen is available for the browser window")
        if find_chrome() is None:
            raise TakeoverRefused("Chrome or Chromium is not installed")
        self.service.driving = True  # from now on no browser step starts
        try:
            if pending(self.service.data_dir):  # the last one isn't undone yet (K2)
                await finish_takeover(self.service.data_dir)
            write_marker(self.service.data_dir, self.engine_site())  # K2, before anything
        except Exception as exc:
            self.service.driving = False
            raise TakeoverRefused(f"The browser can't be handed over now ({exc})") from None
        self._set("waiting")
        self._task = asyncio.get_running_loop().create_task(self._switch_to_owner())

    def stop_turn(self) -> None:
        """ "Stop it now": the running turn's next step fails (the existing owner stop)."""
        self.service.owner_stops += 1
        if self.service.turn_holder is not None:
            self.service.end_turn(self.service.turn_holder)

    async def _switch_to_owner(self) -> None:
        try:
            async with self._lock:  # K4: hand-back cancels this first, then takes the lock
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
                # K4: the launch itself finishes even if the switch is cancelled, so the
                # process is always recorded and hand-back can end it
                self._launching = asyncio.ensure_future(self._launch(root))
                await asyncio.shield(self._launching)
        except Exception as exc:  # S2: anything failed: wipe and go straight back
            log.warning("Take-over didn't start (%s)", type(exc).__name__)
            async with self._lock:
                await self._end_process()
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
        # the search that met the person check (found live: the home page alone lost it)
        home = start_page(root, self.search_template(), self.service.blocked_search())
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
        task = self._task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()  # K4: a switch still in flight never launches after this
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        launching = self._launching
        if launching is not None and not launching.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await launching  # its process is recorded, then ended below
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
        """Back to the assistant's browser, with nothing lasting from the owner's window.
        Never `assistant` with the restore incomplete (K3): then `restoring`, bots still
        refused, the marker kept, and a retry every RETRY_S."""
        # C3 first, so it holds whatever happens next: only the engine's cookies
        self.service.keep_cookies_of = self.engine_site()
        self.service.forget_blocked()  # he had his chance; a new check sets it again
        if self.proxy is not None:
            await self.proxy.stop()
            self.proxy = None
        self.owner_until = None
        if not await self._finish():
            self._set("restoring")
            if self._retry is None or self._retry.done():
                self._retry = asyncio.get_running_loop().create_task(self._retry_restore())
            return
        self.service.driving = False
        self._set("assistant")

    async def _finish(self) -> bool:
        """One restore attempt. K4b: the assistant's own headless Chrome is ended first,
        so it can't keep the profile (a hand-back while still `waiting`)."""
        try:
            await self.service.stop("take_over")
            self.service.keep_cookies_of = await finish_takeover(self.service.data_dir)
        except RestoreIncomplete as exc:
            self.restore_failures += 1
            if self.restore_failures >= SAY_WHY_AFTER:
                self.restore_problem = str(exc)
            log.warning("Take-over: %s; trying again", exc)
            return False
        self.restore_failures, self.restore_problem = 0, None
        return True

    async def _retry_restore(self) -> None:
        while self.mode == "restoring":
            await asyncio.sleep(RETRY_S)
            async with self._lock:
                if self.mode != "restoring":
                    return
                if not await self._finish():
                    self.service.changed()  # the panel may now say why
                    continue
                self.service.driving = False
                self._set("assistant")
