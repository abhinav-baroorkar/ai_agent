"""The brain server: HTTP routes from `AI_reference/api-contract.md`, `/ws`, and the web UI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from sasha import __version__
from sasha.bots import BOT_ID_RE, BotError, BotNotFound, parse_fields
from sasha.connectors import ConnectorError
from sasha.pipeline.llm import OllamaError
from sasha.server.deps import ServerDeps
from sasha.server.security import LocalOnlyMiddleware, allowed_hosts, allowed_origins
from sasha.server.voice import VoiceStages
from sasha.server.ws import Conversation

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parents[1] / "avatar" / "web"


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _json_body(request: Request) -> dict[str, Any]:
    if "application/json" not in request.headers.get("content-type", ""):
        raise ApiError(415, "Send JSON (Content-Type: application/json)")
    try:
        body = await request.json()
    except ValueError:
        raise ApiError(400, "Body is not valid JSON") from None
    if not isinstance(body, dict):
        raise ApiError(400, "Body must be a JSON object")
    return body


def _bot_id(bot_id: str) -> str:
    if not BOT_ID_RE.fullmatch(bot_id):
        raise ApiError(404, f"Unknown bot '{bot_id}'")
    return bot_id


def create_app(deps: ServerDeps, web_dir: Path | None = WEB_DIR) -> FastAPI:
    settings = deps.settings
    server = settings.profile.server
    app = FastAPI(title="brain", version=__version__, docs_url=None, redoc_url=None)
    voice = VoiceStages(stt=deps.stt, tts=deps.tts)  # shared by every socket
    app.add_middleware(
        LocalOnlyMiddleware,
        hosts=allowed_hosts(server.port),
        origins=allowed_origins(server.port, server.allowed_origins),
    )

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.message)

    @app.exception_handler(BotNotFound)
    async def _bot_missing(_: Request, exc: BotNotFound) -> JSONResponse:
        return _error(404, str(exc))

    @app.exception_handler(BotError)
    async def _bot_error(_: Request, exc: BotError) -> JSONResponse:
        return _error(400, str(exc))

    @app.exception_handler(ConnectorError)
    async def _connector_error(_: Request, exc: ConnectorError) -> JSONResponse:
        status = 404 if str(exc).startswith("Unknown connector") else 400
        return _error(status, str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, "Invalid request")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("Unhandled server error: %s", type(exc).__name__)
        return _error(500, "Internal error")

    # --- health and models ---------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        pronouns = settings.assistant.pronouns
        return {
            "ok": True,
            "assistant": {"name": settings.assistant.name, "pronouns": pronouns.model_dump()},
            "profile": settings.profile.name,
            "default_bot": "main",
            "voice": {
                "stt": voice.stt is not None,
                "tts": voice.tts is not None,
                "tts_languages": voice.languages,
            },
        }

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        try:
            return {"models": deps.list_models()}
        except OllamaError as exc:
            raise ApiError(502, str(exc)) from None

    # --- bots --------------------------------------------------------------
    @app.get("/api/bots")
    def list_bots() -> dict[str, Any]:
        return {"bots": [b.model_dump() for b in deps.bots.all()]}

    @app.post("/api/bots", status_code=201)
    async def create_bot(request: Request) -> dict[str, Any]:
        fields = parse_fields(await _json_body(request))
        bot = await run_in_threadpool(deps.bots.create, fields)
        return bot.model_dump()

    @app.patch("/api/bots/{bot_id}")
    async def update_bot(bot_id: str, request: Request) -> dict[str, Any]:
        fields = parse_fields(await _json_body(request))
        bot = await run_in_threadpool(deps.bots.update, _bot_id(bot_id), fields)
        return bot.model_dump()

    @app.delete("/api/bots/{bot_id}")
    def delete_bot(bot_id: str) -> dict[str, Any]:
        deps.bots.delete(_bot_id(bot_id))
        return {"ok": True}

    # --- connectors ----------------------------------------------------------
    @app.get("/api/connectors")
    def list_connectors() -> dict[str, Any]:
        return {"connectors": deps.connectors.all()}

    @app.put("/api/connectors/{connector_id}")
    async def update_connector(connector_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        unknown = sorted(set(body) - {"enabled", "settings"})
        if unknown:
            raise ApiError(400, f"Unknown field(s): {', '.join(unknown)}")
        enabled, new_settings = body.get("enabled"), body.get("settings")
        if enabled is not None and not isinstance(enabled, bool):
            raise ApiError(400, "enabled must be true or false")
        if new_settings is not None and not isinstance(new_settings, dict):
            raise ApiError(400, "settings must be an object")
        return await run_in_threadpool(deps.connectors.update, connector_id, enabled, new_settings)

    @app.post("/api/connectors/{connector_id}/test")
    def test_connector(connector_id: str) -> dict[str, Any]:
        result = deps.connectors.test(connector_id)
        return {"ok": result.ok, "message": result.message}

    # --- conversation ----------------------------------------------------------
    @app.websocket("/ws")
    async def ws(websocket: WebSocket, bot: str = "main", speak: str = "0") -> None:
        await websocket.accept()
        if speak not in ("0", "1"):
            reason = "speak must be 0 or 1"
            await websocket.send_json({"type": "error", "message": reason})
            await websocket.close(code=1008, reason=reason)
            return
        try:
            chosen = deps.bots.get(bot) if BOT_ID_RE.fullmatch(bot) else None
        except BotNotFound:
            chosen = None
        if chosen is None:
            await websocket.send_json({"type": "error", "message": f"Unknown bot '{bot}'"})
            await websocket.close(code=1008)
            return
        assistant, warnings = await run_in_threadpool(deps.make_assistant, chosen)
        for warning in warnings:
            log.warning("Bot %s: %s", chosen.id, warning)
        language = settings.assistant.languages.default
        await Conversation(websocket, assistant, voice, speak == "1", language).run()

    if web_dir is not None and web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
