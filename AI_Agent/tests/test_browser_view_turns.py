"""The live view shows "working" for a whole browsing turn, pushed at once (UI finding)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import sasha.server.browser_view as browser_view
from sasha.browser.proxy import FilteringProxy
from sasha.browser.service import BrowserService
from sasha.browser.tools import BrowserBridge
from sasha.config import load_settings
from sasha.connectors import ConnectorRegistry
from sasha.connectors.browser import BrowserConnector
from sasha.conversation import building_for_bot, current_turn, finish_turn, new_turn
from sasha.server.app import create_app
from sasha.state import JsonStore
from tests.server_fakes import BASE_URL, WS, Harness
from tests.test_browser_view import LOCAL, FakeChrome


def test_working_lasts_the_whole_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_view, "STATE_POLL_S", 5.0)  # changes must be pushed

    async def starter(data_dir: Path, port: int) -> Any:
        return FakeChrome()

    service = BrowserService(tmp_path, starter=starter, proxy_factory=FilteringProxy)
    harness = Harness(load_settings(), tmp_path)
    connector = BrowserConnector(BrowserBridge(service))
    harness.deps.connectors = ConnectorRegistry([connector], JsonStore(tmp_path / "c.json"))
    client = TestClient(create_app(harness.deps, web_dir=None), base_url=BASE_URL, client=LOCAL)
    token = building_for_bot.set("main")
    try:
        tools = {t.name: t for t in connector.build_tools({})}
    finally:
        building_for_bot.reset(token)
    main = harness.bots.get("main")
    with client, client.websocket_connect(f"{WS}/ws/browser", headers={"origin": BASE_URL}) as ws:
        assert ws.receive_json()["running"] is False
        turn = new_turn(("what tabs are open?",), "text")
        token = current_turn.set(turn)
        try:
            for _ in range(2):  # a turn of two tool calls
                assert tools["list_tabs"].handler() == {"tabs": []}
        finally:
            current_turn.reset(token)
            finish_turn(turn.turn_id)
        states = []
        while True:
            m = ws.receive_json()
            if m["type"] != "browser_state":
                continue
            states.append(m)
            if m["running"] and m["agent"] == "idle":
                break
        during = states[:-1]
        assert during and all(s["agent"] == "working" for s in during if s["running"])
        assert all(s["holder_bot"] == {"id": "main", "name": main.name} for s in during)
        assert states[-1]["holder"] is None and states[-1]["holder_bot"] is None


def test_a_stop_just_before_a_step_is_seen_inside_acquire(tmp_path: Path) -> None:
    import asyncio

    from sasha.browser.service import BrowserStoppedError

    async def starter(data_dir: Path, port: int) -> Any:
        return FakeChrome()

    async def main() -> None:
        service = BrowserService(tmp_path, starter=starter, proxy_factory=FilteringProxy)
        began = service.owner_stops  # the turn's count
        await service.stop("owner")  # lands between the turn's check and its step
        with pytest.raises(BrowserStoppedError):
            async with service.acquire("conversation-1", owner_stops=began):
                pass
        assert service.chrome is None  # no fresh Chrome was started
        async with service.acquire("conversation-1", owner_stops=service.owner_stops):
            pass  # the next turn may start it again
        await service.stop()

    asyncio.run(main())


def test_holder_map_trim_is_safe_across_threads() -> None:
    import threading

    from sasha.connectors import browser as connector_module
    from sasha.conversation import building_for_bot

    connector = BrowserConnector(BrowserBridge.__new__(BrowserBridge))
    errors: list[BaseException] = []

    def build(n: int) -> None:
        try:
            for _ in range(200):
                token = building_for_bot.set(f"bot-{n}")
                try:
                    connector.build_tools({})
                finally:
                    building_for_bot.reset(token)
        except BaseException as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=build, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(connector.holders) == connector_module.MAX_HOLDERS
