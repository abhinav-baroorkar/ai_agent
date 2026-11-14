"""Keep other web pages from driving the assistant.

v0 has no auth and listens on loopback only, so the threats are a web page in the
user's own browser:
- **DNS rebinding** (evil.example resolving to 127.0.0.1): refused by checking the
  `Host` header against the server's own loopback names.
- **Cross-site WebSocket / form posts**: refused by checking `Origin` (and
  `Sec-Fetch-Site`) on the WebSocket and on every state-changing HTTP request.
Clients that send no `Origin` (curl, satellites, tests) are not browsers and are allowed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_hosts(port: int) -> set[str]:
    return {f"{name}:{port}" for name in LOOPBACK_NAMES}


def allowed_origins(port: int, extra: Iterable[str] = ()) -> set[str]:
    own = {f"http://{name}:{port}" for name in LOOPBACK_NAMES}
    return own | {o.rstrip("/") for o in extra}


def is_loopback(host: str) -> bool:
    return host.strip("[]") in LOOPBACK_HOSTS


class LocalOnlyMiddleware:
    """Pure ASGI middleware, so it covers both HTTP and WebSocket requests."""

    def __init__(self, app: ASGIApp, hosts: set[str], origins: set[str]) -> None:
        self.app = app
        self.hosts = hosts
        self.origins = origins

    def _refusal(self, scope: Scope) -> str | None:
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        if headers.get("host", "").lower() not in self.hosts:
            return "Host not allowed"
        is_ws = scope["type"] == "websocket"
        if not is_ws and scope.get("method", "GET") in SAFE_METHODS:
            return None
        origin = headers.get("origin")
        if origin is not None and origin.rstrip("/") not in self.origins:
            return "Origin not allowed"
        if headers.get("sec-fetch-site") == "cross-site" and origin is None:
            return "Cross-site request not allowed"
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        reason = self._refusal(scope)
        if reason is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # Closing before accept makes the handshake fail with HTTP 403.
            await send({"type": "websocket.close", "code": 1008, "reason": reason})
            return
        body = json.dumps({"error": reason}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
