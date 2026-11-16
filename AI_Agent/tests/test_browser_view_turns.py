"""The live view shows "working" for a whole browsing turn, pushed at once (UI finding)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import sasha.server.browser_view as browser_view
from sasha.browser.proxy import FilteringProxy
from sasha.browser.service import BrowserService
from sasha.browser.tools import BrowserBridge
from sasha.config import load_settings
from sasha.connectors import ConnectorRegistry
from sasha.connectors.browser import BrowserConnector
from sasha.conversation import building_for_bot, current_turn, finish_turn, new_turn
from sasha.server.app import create_app
from sasha.server.window_token import WindowToken
from sasha.state import JsonStore
from tests.server_fakes import BASE_URL, WS, Harness
from tests.test_browser_view import LOCAL, TOKEN, FakeChrome, WindowClient


def test_working_lasts_the_whole_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_view, "STATE_POLL_S", 5.0)  # changes must be pushed

    async def starter(data_dir: Path, port: int) -> Any:
        return FakeChrome()

    service = BrowserService(tmp_path, starter=starter, proxy_factory=FilteringProxy)
    harness = Harness(load_settings(), tmp_path)
    connector = BrowserConnector(BrowserBridge(service))
    harness.deps.connectors = ConnectorRegistry([connector], JsonStore(tmp_path / "c.json"))
    harness.deps.window_token = WindowToken(TOKEN)
    client = WindowClient(create_app(harness.deps, web_dir=None), base_url=BASE_URL, client=LOCAL)
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


def test_one_turn_holds_the_browser_and_a_new_holder_gets_no_old_tabs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review W1: B's step is refused while A's turn holds the browser, even between A's
    steps; after A's turn ends B proceeds, without A's tabs; the view shows A working."""
    from sasha.browser.service import BUSY, BrowserBusyError

    monkeypatch.setattr(browser_view, "STATE_POLL_S", 5.0)
    chrome = FakeChrome()

    async def starter(data_dir: Path, port: int) -> Any:
        return chrome

    service = BrowserService(tmp_path, starter=starter, proxy_factory=FilteringProxy)
    harness = Harness(load_settings(), tmp_path)
    connector = BrowserConnector(BrowserBridge(service))
    harness.deps.connectors = ConnectorRegistry([connector], JsonStore(tmp_path / "c.json"))
    client = TestClient(create_app(harness.deps, web_dir=None), base_url=BASE_URL, client=LOCAL)

    async def step(holder: str) -> None:
        async with service.acquire(holder, turn=True):
            pass

    with client, client.websocket_connect(f"{WS}/ws/browser", headers={"origin": BASE_URL}) as ws:
        assert ws.receive_json()["running"] is False
        client.portal.call(step, "conversation-a")  # A's first step
        for _ in range(3):  # B tries between A's steps
            with pytest.raises(BrowserBusyError, match="try again shortly"):
                client.portal.call(step, "conversation-b")
            client.portal.call(step, "conversation-a")
        assert service.turn_holder == "conversation-a" and BUSY.startswith("The browser")
        client.portal.call(service.end_turn, "conversation-a")  # A's turn ends
        client.portal.call(step, "conversation-b")  # now B proceeds
        closed = [p["targetId"] for p in chrome.pipe.named("Target.closeTarget")]
        assert closed == ["t1", "t2"] and chrome.tab_targets == []  # A's tabs are gone
        assert service.holder_epoch == 1
        states = []
        while True:
            m = ws.receive_json()
            if m["type"] == "browser_state":
                states.append(m)
                if m["holder"] == "conversation-b":
                    break
        before_b = [s for s in states if s["running"] and s["holder"] != "conversation-b"]
        assert before_b and before_b[0]["holder"] == "conversation-a"
        shown = {(s["holder"], s["agent"]) for s in before_b}  # A working, or idle between
        assert shown <= {("conversation-a", "working"), (None, "idle")}


def test_a_hung_turn_releases_the_browser_after_the_backstop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S1: a turn that claims the browser and never ends doesn't keep it."""
    import asyncio

    import sasha.browser.service as service_module
    from sasha.browser.service import BrowserBusyError

    monkeypatch.setattr(service_module, "MAX_TURN_HOLD_S", 0.2)

    async def starter(data_dir: Path, port: int) -> Any:
        return FakeChrome()

    async def main() -> None:
        service = BrowserService(tmp_path, starter=starter, proxy_factory=FilteringProxy)
        async with service.acquire("conversation-a", turn=True):
            pass  # A's turn claims the browser, then hangs: its end hook never runs
        with pytest.raises(BrowserBusyError):
            async with service.acquire("conversation-b", turn=True):
                pass
        await asyncio.sleep(0.4)
        assert service.turn_holder is None  # released by the backstop
        async with service.acquire("conversation-b", turn=True):
            assert service.turn_holder == "conversation-b"
        service.end_turn("conversation-b")  # a normal end cancels B's backstop
        await asyncio.sleep(0.4)
        assert service.turn_holder is None and service._hold_timer is None
        await service.stop()

    asyncio.run(main())


def test_the_backstop_covers_a_whole_turn_budget() -> None:
    from sasha.browser.service import MAX_TURN_HOLD_S
    from sasha.browser.tools import OP_TIMEOUT_S, SECONDS_PER_TURN

    assert MAX_TURN_HOLD_S >= SECONDS_PER_TURN + OP_TIMEOUT_S  # never cuts a live turn short
