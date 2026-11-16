"""Pictures of the agent's current tab for the window's live view (contract v0.4).

`Screencast` attaches its own DevTools session to one tab and runs
`Page.startScreencast` (JPEG). Flow control: one frame in flight. A frame goes to the
window; Chrome's frame is acknowledged (so Chrome sends the next one) only after the
window has acknowledged ours and at least `MIN_INTERVAL_S` has passed, so at most about
5 frames a second and a slow window slows the stream instead of queueing. Frames are
never logged; only counts are kept.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sasha.browser.cdp import CDPError, CDPPipe

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 0.2  # at most about 5 frames a second
RESTART_INTERVAL_S = 0.5  # at most two screencast restarts a second (navigation loops)
MAX_FRAME_BYTES = 512 * 1024
MAX_WIDTH = 1280
QUALITY = 60
Emit = Callable[[dict[str, Any]], Awaitable[None]]


def is_jpeg(data_b64: str) -> bool:
    """Valid base64, a JPEG start marker, and within the size limit."""
    if len(data_b64) > MAX_FRAME_BYTES * 4 // 3 + 4:
        return False
    try:
        head = base64.b64decode(data_b64[:8], validate=True)
    except (binascii.Error, ValueError):
        return False
    return head[:3] == b"\xff\xd8\xff"


async def attach(pipe: CDPPipe, target_id: str) -> str:
    attached = await pipe.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    return str(attached["sessionId"])


class Screencast:
    def __init__(self, pipe: CDPPipe, target_id: str, tab: int, emit: Emit) -> None:
        self.pipe, self.target_id, self.tab, self.emit = pipe, target_id, tab, emit
        self.session: str | None = None
        self.seq = 0
        self.in_flight: int | None = None  # our frame the window hasn't acknowledged
        self.chrome_acks: list[int] = []  # Chrome frames to acknowledge after the window
        self.last_sent = 0.0
        self.sent = self.dropped = self.restarts = 0
        self._restart_pending = self._ack_pending = False
        self._last_restart = 0.0
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self.session = await attach(self.pipe, self.target_id)
        self.pipe.on("Page.screencastFrame", self._on_frame)
        # Page events are needed: a navigation that moves the page to another renderer
        # (about:blank -> a site) ends Chrome's screencast, so it is started again then.
        self.pipe.on("Page.frameNavigated", self._on_navigated)
        await self.pipe.call("Page.enable", session_id=self.session)
        await self._start_cast()

    async def _start_cast(self) -> None:
        if self.session is None:
            return
        self.in_flight, self.chrome_acks = None, []  # Chrome starts counting again
        await self.pipe.call(
            "Page.startScreencast",
            {"format": "jpeg", "quality": QUALITY, "maxWidth": MAX_WIDTH, "maxHeight": MAX_WIDTH},
            session_id=self.session,
        )

    def _on_navigated(self, params: dict[str, Any]) -> None:
        frame = params.get("frame") or {}
        if self.session is None or params.get("_session") != self.session or frame.get("parentId"):
            return
        if self._restart_pending:  # coalesced: one pending restart at a time (review W2)
            return
        self._restart_pending = True
        self._spawn(self._restart())

    async def _restart(self) -> None:
        try:
            wait = self._last_restart + RESTART_INTERVAL_S - time.monotonic()
            if wait > 0:  # at most one restart per RESTART_INTERVAL_S
                await asyncio.sleep(wait)
            self._last_restart = time.monotonic()
            self.restarts += 1
            with contextlib.suppress(CDPError):
                await self._start_cast()
        finally:
            self._restart_pending = False

    async def stop(self) -> None:
        self.pipe.off("Page.screencastFrame", self._on_frame)
        self.pipe.off("Page.frameNavigated", self._on_navigated)
        for task in self._tasks:
            task.cancel()
        if self.session is not None:
            with contextlib.suppress(CDPError):
                await self.pipe.call("Page.stopScreencast", session_id=self.session, timeout=2)
            with contextlib.suppress(CDPError):
                await self.pipe.call(
                    "Target.detachFromTarget", {"sessionId": self.session}, timeout=2
                )
            self.session = None

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_frame(self, params: dict[str, Any]) -> None:
        if self.session is None or params.get("_session") != self.session:
            return
        self._spawn(self._frame(params))

    async def _frame(self, params: dict[str, Any]) -> None:
        chrome_id = int(params.get("sessionId", 0))
        data = str(params.get("data", ""))
        too_soon = time.monotonic() - self.last_sent < MIN_INTERVAL_S  # whatever restarts did
        if self.in_flight is not None or too_soon or not is_jpeg(data):
            self.dropped += 1
            self.chrome_acks.append(chrome_id)
            if self.in_flight is None and not self._ack_pending:
                # ask for the next frame once the interval has passed (not at once, which
                # would spin), or now for a bad frame outside it
                self._ack_pending = True
                self._spawn(self._ack_after(self.last_sent + MIN_INTERVAL_S - time.monotonic()))
            return
        meta = params.get("metadata") or {}
        self.seq += 1
        self.in_flight = self.seq
        self.chrome_acks.append(chrome_id)
        self.last_sent = time.monotonic()
        self.sent += 1
        await self.emit(
            {
                "type": "browser_frame",
                "tab": self.tab,
                "seq": self.seq,
                "w": int(meta.get("deviceWidth", 0)),
                "h": int(meta.get("deviceHeight", 0)),
                "jpeg": data,
            }
        )

    async def window_ack(self, seq: int) -> None:
        if seq != self.in_flight:
            return
        wait = self.last_sent + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:  # still "in flight" until then, so frames meanwhile are dropped
            await asyncio.sleep(wait)
        if self.in_flight == seq:
            self.in_flight = None
        await self._ack_chrome()

    async def _ack_after(self, wait: float) -> None:
        try:
            if wait > 0:
                await asyncio.sleep(wait)
            await self._ack_chrome()
        finally:
            self._ack_pending = False

    async def _ack_chrome(self) -> None:
        acks, self.chrome_acks = self.chrome_acks, []
        if self.session is None:
            return
        for chrome_id in acks:
            with contextlib.suppress(CDPError):
                await self.pipe.call(
                    "Page.screencastFrameAck", {"sessionId": chrome_id}, session_id=self.session
                )
