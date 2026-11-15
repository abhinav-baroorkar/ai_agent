"""`/api/timers` (contract v0.3). Behind the app's Host and Origin checks like every
route; a POST body must be JSON (dismiss and say may have none), D5."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from sasha.server.timers.hub import Hub
from sasha.server.timers.service import TimerError, TimerService

STATUS = {"unknown": 404, "use_dismiss": 409, "not_ringing": 409, "not_active": 409}
JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def _refused(exc: TimerError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=STATUS.get(exc.reason, 400))


def add_timer_routes(app: FastAPI, service: TimerService, hub: Hub, json_body: JsonBody) -> None:
    async def body_or_none(request: Request) -> dict[str, Any]:
        """No body and no content type (the Home card's dismiss and say) is fine; any
        body must be JSON, so a cross-site form post is still refused (415)."""
        if "content-type" not in request.headers and not await request.body():
            return {}
        return await json_body(request)

    @app.exception_handler(TimerError)
    async def _timer_error(_: Request, exc: TimerError) -> JSONResponse:
        return _refused(exc)

    @app.get("/api/timers")
    def list_timers() -> dict[str, Any]:
        return service.snapshot()

    @app.delete("/api/timers/{item_id}")
    def delete_timer(item_id: str) -> dict[str, Any]:
        service.remove(item_id)
        return {"ok": True}

    @app.post("/api/timers/{item_id}/dismiss")
    async def dismiss(item_id: str, request: Request) -> dict[str, Any]:
        await body_or_none(request)
        item = await run_in_threadpool(service.dismiss, item_id)
        return item.to_api(service.zone)

    @app.post("/api/timers/{item_id}/snooze")
    async def snooze(item_id: str, request: Request) -> dict[str, Any]:
        body = await json_body(request)
        minutes = body.get("minutes", service.config.snooze_minutes)
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 60:
            return JSONResponse({"error": "minutes must be a whole number from 1 to 60"}, 400)  # type: ignore[return-value]
        item = await run_in_threadpool(service.snooze, item_id, minutes)
        return item.to_api(service.zone)

    @app.post("/api/timers/{item_id}/say")
    async def say(item_id: str, request: Request) -> dict[str, Any]:
        await body_or_none(request)
        with service.lock:
            item = service.get(item_id)
        if not hub.say_text(item):
            return JSONResponse({"error": "Nothing connected can speak it"}, 409)  # type: ignore[return-value]
        return {"ok": True}
