"""`/api/memory` (contract v0.6): the owner's Memory list. Behind the app's Host and
Origin checks like every route; every POST/PATCH body must be JSON, so a cross-site
form post is refused (415). Note text goes to the page as text (the UI's sinks)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from sasha.memory.store import MemoryError, MemoryStore

STATUS = {"unknown": 404, "pending": 409, "full": 409}
JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def add_memory_routes(app: FastAPI, store: MemoryStore, json_body: JsonBody) -> None:
    @app.exception_handler(MemoryError)
    async def _memory_error(_: Request, exc: MemoryError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=STATUS.get(exc.reason, 400))

    @app.get("/api/memory")
    def list_notes() -> dict[str, Any]:
        return store.snapshot()

    @app.post("/api/memory/{note_id}/keep")
    async def keep(note_id: str, request: Request) -> dict[str, Any]:
        await json_body(request)
        return (await run_in_threadpool(store.keep, note_id)).to_api()

    @app.patch("/api/memory/{note_id}")
    async def change(note_id: str, request: Request) -> dict[str, Any]:
        body = await json_body(request)
        value = body.get("say_aloud")
        if set(body) != {"say_aloud"} or not isinstance(value, bool):
            return JSONResponse({"error": "Only say_aloud (true or false) can change"}, 400)  # type: ignore[return-value]
        return (await run_in_threadpool(store.set_say_aloud, note_id, value)).to_api()

    @app.delete("/api/memory/{note_id}")
    def delete(note_id: str) -> dict[str, Any]:
        store.delete(note_id, "screen")
        return {"ok": True}

    @app.post("/api/memory/forget")
    async def forget(request: Request) -> dict[str, Any]:
        """{"minutes": N}: the notes made in the last N minutes and every open
        conversation's turns from then; {"everything": true}: all notes. The page asks
        the owner to confirm first. The report lists exactly what went."""
        body = await json_body(request)
        if body == {"everything": True}:
            items = [{"id": n.id, "text": n.text} for n in store.all()]
            await run_in_threadpool(store.clear)
            return {"count": len(items), "items": items, "turns": {}}
        minutes = body.get("minutes")
        if set(body) != {"minutes"} or isinstance(minutes, bool) or not isinstance(minutes, int):
            return JSONResponse({"error": 'Send {"minutes": N} or {"everything": true}'}, 400)  # type: ignore[return-value]
        if not 1 <= minutes <= store.config.max_forget_minutes:
            return JSONResponse({"error": "minutes is out of range"}, 400)  # type: ignore[return-value]
        return await run_in_threadpool(store.forget_window, minutes * 60.0)
