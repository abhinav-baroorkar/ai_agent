"""Start-up and shut-down hooks that tie the browser to the brain server's event loop.

Chrome must be spawned from the loop's own (main) thread, so the bridge learns the
loop when the server starts, and the browser is stopped when the server stops.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sasha.browser.tools import BrowserBridge


def find_bridge(connectors: Any) -> BrowserBridge | None:
    try:
        connector = connectors.get("browser")
    except Exception:  # no browser connector registered
        return None
    bridge = getattr(connector, "bridge", None)
    return bridge if isinstance(bridge, BrowserBridge) else None


def attach_browser(app: Any, connectors: Any) -> None:
    bridge = find_bridge(connectors)
    if bridge is None:
        return

    async def on_start() -> None:
        bridge.attach(asyncio.get_running_loop())

    async def on_stop() -> None:
        await bridge.stop()

    app.router.on_startup.append(on_start)
    app.router.on_shutdown.append(on_stop)
