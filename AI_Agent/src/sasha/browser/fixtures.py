"""Serve fixed pages on `test.invalid` through CDP `Fetch`, for tests and benches only.

`https://test.invalid/<path>` never reaches the network: Chrome pauses the request and
this hook answers it with our own HTML, so tests and the injection bench run against
real Chrome without contacting any site. Production never installs this hook.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from sasha.browser.cdp import CDPPipe
from sasha.browser.pages import TabHook

TEST_ORIGIN = "https://test.invalid"


def serve_pages(pages: Mapping[str, str], hits: list[str] | None = None) -> TabHook:
    """A tab hook answering `https://test.invalid<path>` with `pages[path]` (else 404).
    Every requested path is appended to `hits`, if given (to see what was followed)."""

    async def hook(pipe: CDPPipe, session: str) -> None:
        def paused(params: dict[str, Any]) -> None:
            if params.get("_session") != session:
                return
            parts = urlsplit(str(params.get("request", {}).get("url", "")))
            path = parts.path or "/"
            if hits is not None:
                hits.append(path + (f"?{parts.query}" if parts.query else ""))
            html = pages.get(path)
            body = html if html is not None else "<title>Not found</title>not found"
            asyncio.get_running_loop().create_task(
                pipe.call(
                    "Fetch.fulfillRequest",
                    {
                        "requestId": params["requestId"],
                        "responseCode": 200 if html is not None else 404,
                        "body": base64.b64encode(body.encode()).decode(),
                        "responseHeaders": [
                            {"name": "Content-Type", "value": "text/html; charset=utf-8"}
                        ],
                    },
                    session_id=session,
                )
            )

        pipe.on("Fetch.requestPaused", paused)
        await pipe.call(
            "Fetch.enable", {"patterns": [{"urlPattern": "*://test.invalid/*"}]}, session_id=session
        )

    return hook
