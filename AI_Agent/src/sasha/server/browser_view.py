"""The browser live view (contract v0.4): `/api/browser*` and the `/ws/browser` socket.

Watching only. The window gets the agent's tabs as hostname plus short title, and the
current tab as JPEG frames (`browser/screencast.py`), and can stop the browser at any
time. Loopback clients only, behind the server's Host and Origin checks; the socket also
needs an `Origin` header (only the window opens it) and has its own slot. Nothing about
URLs, sites, titles or frames is logged: counts only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from sasha.browser.cdp import CDPError
from sasha.browser.hooks import find_bridge
from sasha.browser.pages import domain_of
from sasha.browser.reader import clean
from sasha.browser.screencast import Screencast, snapshot
from sasha.browser.service import BrowserService
from sasha.server.security import is_loopback

log = logging.getLogger(__name__)

BROWSER_SOCKETS = 1  # the live view's own slot, apart from max_sockets and the listener
STATE_POLL_S = 0.5
TITLE_CHARS = 80
SAFE_FETCH_SITES = {None, "same-origin", "none"}


class BrowserView:
    def __init__(self, service: BrowserService | None, enabled: Any) -> None:
        self.service = service
        self.enabled = enabled  # () -> bool: the connector is switched on
        self.viewers = 0

    def running(self) -> bool:
        chrome = self.service.chrome if self.service is not None else None
        return chrome is not None and chrome.process.returncode is None

    async def state(self) -> dict[str, Any]:
        service = self.service
        guard = {"allowed": 0, "refused": {}, "background_refused": 0}
        out: dict[str, Any] = {
            "enabled": bool(self.enabled()),
            "running": False,
            "agent": "idle",
            "holder": None,
            "tabs": [],
            "guard": guard,
        }
        if service is None or not self.running():
            return out
        chrome = service.chrome
        assert chrome is not None
        out["running"] = True
        out["agent"] = "working" if service.holder is not None else "idle"
        out["holder"] = service.holder
        if service.proxy is not None:
            s = service.proxy.stats
            guard.update(
                allowed=s.allowed, refused=dict(s.refused), background_refused=s.background_refused
            )
        infos: dict[str, dict[str, Any]] = {}
        with contextlib.suppress(CDPError):
            listed = await chrome.pipe.call("Target.getTargets", timeout=3)
            infos = {str(t.get("targetId")): t for t in listed.get("targetInfos", [])}
        for n, target in enumerate(list(chrome.tab_targets), 1):
            info = infos.get(target, {})
            out["tabs"].append(
                {
                    "tab": n,
                    "site": domain_of(str(info.get("url", ""))),
                    "title": clean(str(info.get("title", "")), TITLE_CHARS),
                    "current": target == chrome.current_target,
                }
            )
        return out

    async def frame(self) -> bytes | None:
        if self.service is None or not self.running():
            return None
        chrome = self.service.chrome
        assert chrome is not None
        if chrome.current_target is None:
            return None
        try:
            return await snapshot(chrome.pipe, chrome.current_target)
        except CDPError:
            return None

    async def stop(self) -> None:
        if self.service is not None:
            await self.service.stop("owner")

    # --- the socket -------------------------------------------------------------------
    async def serve(self, websocket: WebSocket) -> None:
        lock = asyncio.Lock()
        watching = False
        cast: Screencast | None = None
        counts = {"sent": 0, "dropped": 0}
        acks: set[asyncio.Future[None]] = set()

        async def emit(message: dict[str, Any]) -> None:
            async with lock:
                await websocket.send_json(message)

        async def end_cast() -> None:
            nonlocal cast
            if cast is not None:
                counts["sent"] += cast.sent
                counts["dropped"] += cast.dropped
                await cast.stop()
                cast = None

        async def follow() -> None:
            """Keep the state and the screencast in step with the browser."""
            nonlocal cast
            last: dict[str, Any] | None = None
            was_running = False
            while True:
                state = await self.state()
                if was_running and not state["running"]:
                    await end_cast()
                    reason = self.service.last_stop if self.service is not None else None
                    await emit({"type": "browser_stopped", "reason": reason or "crashed"})
                was_running = state["running"]
                if state != last:
                    await emit({"type": "browser_state", **state})
                    last = state
                chrome = self.service.chrome if self.service is not None else None
                target = chrome.current_target if chrome is not None and self.running() else None
                if cast is not None and (not watching or cast.target_id != target):
                    await end_cast()
                if watching and cast is None and chrome is not None and target is not None:
                    tab = chrome.tab_targets.index(target) + 1
                    cast = Screencast(chrome.pipe, target, tab, emit)
                    try:
                        await cast.start()
                    except CDPError:
                        await end_cast()
                await asyncio.sleep(STATE_POLL_S)

        follower = asyncio.create_task(follow())
        try:
            while True:
                message = await websocket.receive_json()
                kind = message.get("type") if isinstance(message, dict) else None
                if kind == "browser_watch":
                    watching = message.get("on") is True
                elif kind == "browser_ack":
                    seq = message.get("seq")
                    if cast is not None and isinstance(seq, int):
                        acks.add(task := asyncio.ensure_future(cast.window_ack(seq)))
                        task.add_done_callback(acks.discard)
                elif kind == "browser_stop":
                    await end_cast()
                    await self.stop()
                else:
                    await emit({"type": "error", "message": "Unknown message"})
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            follower.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await follower
            await end_cast()
            log.info("Browser view closed: %d frames sent, %d dropped", *counts.values())


def _fetch_site_ok(request: Request) -> bool:
    return request.headers.get("sec-fetch-site") in SAFE_FETCH_SITES


def _local(host: str | None) -> bool:
    return host is not None and is_loopback(host)


def add_browser_routes(app: FastAPI, deps: Any) -> BrowserView:
    bridge = find_bridge(deps.connectors)

    def enabled() -> bool:
        rows = [c for c in deps.connectors.all() if c.get("id") == "browser"]
        return bool(rows and rows[0].get("enabled"))

    view = BrowserView(bridge.service if bridge is not None else None, enabled)

    def refused(request: Request) -> JSONResponse | None:
        if not _local(request.client.host if request.client else None):
            return JSONResponse({"error": "Loopback only"}, status_code=403)
        if not _fetch_site_ok(request):
            return JSONResponse({"error": "Cross-site request not allowed"}, status_code=403)
        return None

    @app.get("/api/browser", response_model=None)
    async def browser_state(request: Request) -> Any:
        return refused(request) or await view.state()

    @app.get("/api/browser/frame", response_model=None)
    async def browser_frame(request: Request) -> Any:
        if (no := refused(request)) is not None:
            return no
        jpeg = await view.frame()
        if jpeg is None:
            return JSONResponse({"error": "The browser isn't running"}, status_code=404)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/browser/stop", response_model=None)
    async def browser_stop(request: Request) -> Any:
        if (no := refused(request)) is not None:
            return no
        body = await request.body()
        media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if body and media != "application/json":
            return JSONResponse({"error": "Send JSON or no body"}, status_code=415)
        await view.stop()
        return {"ok": True}

    @app.websocket("/ws/browser")
    async def browser_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        client = websocket.client.host if websocket.client else None
        if not _local(client) or websocket.headers.get("origin") is None:
            await websocket.close(code=1008, reason="Only the window may watch the browser")
            return
        if view.viewers >= BROWSER_SOCKETS:
            await websocket.close(code=1013, reason="The browser is already being watched")
            return
        view.viewers += 1
        try:
            await view.serve(websocket)
        finally:
            view.viewers -= 1

    return view
