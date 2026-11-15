"""The browser live view (contract v0.4) with a fake Chrome: frames, watch, stop, checks."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import sasha.browser.screencast as screencast
import sasha.server.browser_view as browser_view
from sasha.browser.proxy import FilteringProxy
from sasha.browser.service import BrowserService
from sasha.browser.tools import BrowserBridge
from sasha.config import load_settings
from sasha.connectors import ConnectorRegistry
from sasha.connectors.browser import BrowserConnector
from sasha.server.app import create_app
from sasha.state import JsonStore
from tests.server_fakes import BASE_URL, WS, Harness

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"x" * 64).decode()
LOCAL = ("127.0.0.1", 50000)


class FakeProcess:
    returncode: int | None = None


class FakePipe:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []
        self.listeners: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self.attach_failures = 0

    def on(self, method: str, listener: Callable[[dict[str, Any]], None]) -> None:
        self.listeners.setdefault(method, []).append(listener)

    def off(self, method: str, listener: Callable[[dict[str, Any]], None]) -> None:
        if listener in self.listeners.get(method, []):  # tolerant, like CDPPipe.off
            self.listeners[method].remove(listener)

    def emit(self, method: str, params: dict[str, Any]) -> None:
        for listener in list(self.listeners.get(method, [])):
            listener(params)

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float = 10,
    ) -> dict[str, Any]:
        self.calls.append((method, params or {}, session_id))
        if method == "Target.getTargets":
            infos = [
                {"targetId": "t1", "url": "https://news.example/a?secret=1", "title": "News\x07"},
                {"targetId": "t2", "url": "https://shop.example/", "title": "x" * 200},
            ]
            return {"targetInfos": infos}
        if method == "Target.attachToTarget":
            if self.attach_failures > 0:  # a reply without a sessionId (KeyError later)
                self.attach_failures -= 1
                return {}
            return {"sessionId": "view-1"}
        return {}

    def named(self, method: str) -> list[dict[str, Any]]:
        return [p for m, p, _ in self.calls if m == method]


class FakeChrome:
    def __init__(self) -> None:
        self.process = FakeProcess()
        self.pipe = FakePipe()
        self.tab_targets = ["t1", "t2"]
        self.current_target: str | None = "t1"
        self.page_hosts: set[str] = set()
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True
        self.process.returncode = 0


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(browser_view, "STATE_POLL_S", 0.02)
    monkeypatch.setattr(screencast, "MIN_INTERVAL_S", 0.0)
    chrome = FakeChrome()

    async def starter(data_dir: Path, port: int) -> Any:
        return chrome

    service = BrowserService(tmp_path, starter=starter, proxy_factory=FilteringProxy)
    harness = Harness(load_settings(), tmp_path)
    connector = BrowserConnector(BrowserBridge(service))
    harness.deps.connectors = ConnectorRegistry(
        [connector], JsonStore(tmp_path / "connectors.json")
    )
    client = TestClient(create_app(harness.deps, web_dir=None), base_url=BASE_URL, client=LOCAL)
    client.harness = harness  # type: ignore[attr-defined]
    client.connector = connector  # type: ignore[attr-defined]
    with client:
        yield client, service, chrome


def _start(client: TestClient, service: BrowserService) -> None:
    async def run() -> None:
        async with service.acquire("conversation-1"):
            pass

    client.portal.call(run)  # type: ignore[union-attr]


def _until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 3
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_state_shows_hostnames_and_short_titles_only(setup: Any) -> None:
    client, service, _ = setup
    off = client.get("/api/browser").json()
    assert off["running"] is False and off["tabs"] == [] and off["enabled"] is False
    assert client.get("/api/browser/frame").status_code == 404  # no picture route (L1)
    _start(client, service)
    state = client.get("/api/browser").json()
    assert state["running"] is True and state["agent"] == "idle"
    assert state["tabs"][0] == {"tab": 1, "site": "news.example", "title": "News", "current": True}
    assert len(state["tabs"][1]["title"]) <= 80 and not state["tabs"][1]["current"]
    assert "secret" not in str(state) and "https://" not in str(state)
    assert state["guard"] == {"allowed": 0, "refused": {}, "background_refused": 0}
    assert client.get("/api/browser/frame").status_code == 404  # not even while running
    assert client.get("/api/health").json()["limits"]["browser_sockets"] == 1


def test_http_checks(setup: Any) -> None:
    client, service, chrome = setup
    cross = {"sec-fetch-site": "cross-site"}
    assert client.get("/api/browser", headers=cross).status_code == 403
    assert client.get("/api/browser", headers={"sec-fetch-site": "same-site"}).status_code == 403
    assert (
        client.post("/api/browser/stop", headers={"origin": "http://evil.example"}).status_code
        == 403
    )
    plain = {"content-type": "text/plain"}
    assert client.post("/api/browser/stop", content=b"stop", headers=plain).status_code == 415
    with TestClient(client.app, base_url=BASE_URL, client=("203.0.113.5", 1)) as remote:
        assert remote.get("/api/browser").status_code == 403
    _start(client, service)
    assert client.post("/api/browser/stop").json() == {"ok": True}
    assert chrome.stopped and service.owner_stops == 1 and service.last_stop == "owner"


def _frame(client: TestClient, chrome: FakeChrome, chrome_id: int) -> None:
    params = {
        "data": JPEG,
        "sessionId": chrome_id,
        "metadata": {"deviceWidth": 800, "deviceHeight": 600},
        "_session": "view-1",
    }
    client.portal.call(lambda: _emit(chrome, params))  # type: ignore[union-attr]


async def _emit(chrome: FakeChrome, params: dict[str, Any]) -> None:
    chrome.pipe.emit("Page.screencastFrame", params)


def test_frames_flow_only_while_watching_one_at_a_time(setup: Any) -> None:
    client, service, chrome = setup
    _start(client, service)
    with client.websocket_connect(f"{WS}/ws/browser", headers={"origin": BASE_URL}) as ws:
        first = ws.receive_json()
        assert first["type"] == "browser_state" and first["running"] is True
        time.sleep(0.1)
        assert not chrome.pipe.named("Page.startScreencast")  # nothing while not watching
        ws.send_json({"type": "browser_watch", "on": True})
        _until(lambda: bool(chrome.pipe.named("Page.startScreencast")))
        start = chrome.pipe.named("Page.startScreencast")[0]
        assert start["format"] == "jpeg" and start["maxWidth"] == 1280
        _frame(client, chrome, 11)
        got = ws.receive_json()
        assert got == {
            "type": "browser_frame",
            "tab": 1,
            "seq": 1,
            "w": 800,
            "h": 600,
            "jpeg": JPEG,
        }
        _frame(client, chrome, 12)  # arrives before the window's ack: dropped, not queued
        time.sleep(0.05)
        assert not chrome.pipe.named("Page.screencastFrameAck")  # Chrome waits for the window
        ws.send_json({"type": "browser_ack", "seq": 1})
        _until(lambda: len(chrome.pipe.named("Page.screencastFrameAck")) == 2)
        _frame(client, chrome, 13)
        assert ws.receive_json()["seq"] == 2
        ws.send_json({"type": "browser_watch", "on": False})
        _until(lambda: bool(chrome.pipe.named("Page.stopScreencast")))
        assert chrome.pipe.named("Target.detachFromTarget")
        ws.send_json({"type": "browser_stop"})
        stopped = ws.receive_json()
        assert stopped == {"type": "browser_stopped", "reason": "owner"}
        after = ws.receive_json()
        assert after["type"] == "browser_state" and after["running"] is False
    assert service.owner_stops == 1


def test_socket_checks_and_its_own_slot(setup: Any) -> None:
    client = setup[0]
    url, window = f"{WS}/ws/browser", {"origin": BASE_URL}
    with pytest.raises(WebSocketDisconnect) as no_origin, client.websocket_connect(url) as ws:
        ws.receive_json()
    assert no_origin.value.code == 1008
    evil = {"origin": "http://evil.example"}
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(url, headers=evil):
        pass
    with TestClient(client.app, base_url=BASE_URL, client=("198.51.100.2", 1)) as remote:
        with (
            pytest.raises(WebSocketDisconnect) as far,
            remote.websocket_connect(url, headers=window) as ws,
        ):
            ws.receive_json()
        assert far.value.code == 1008
    with client.websocket_connect(f"{WS}/ws/browser", headers={"origin": BASE_URL}) as first:
        assert first.receive_json()["running"] is False
        with (
            pytest.raises(WebSocketDisconnect) as second,
            client.websocket_connect(url, headers=window) as ws,
        ):
            ws.receive_json()
        assert second.value.code == 1013


def test_idle_stop_is_reported(setup: Any) -> None:
    client, service, _ = setup
    _start(client, service)
    with client.websocket_connect(f"{WS}/ws/browser", headers={"origin": BASE_URL}) as ws:
        assert ws.receive_json()["running"] is True

        async def idle() -> None:
            service.last_stop = "idle"
            await service._shutdown()

        client.portal.call(idle)  # type: ignore[union-attr]
        assert ws.receive_json() == {"type": "browser_stopped", "reason": "idle"}
    assert service.owner_stops == 0


def test_owner_stop_fails_the_turns_next_step() -> None:
    from sasha.browser.tools import BrowserConversation
    from sasha.conversation import current_turn, new_turn
    from sasha.tools import ToolError

    class Service:
        owner_stops = 0

        def begin_turn(self, holder: str) -> None:
            pass

        def end_turn(self, holder: str) -> None:
            pass

    class Bridge:
        service = Service()

        def call_soon(self, fn: Callable[..., None], *args: Any) -> None:
            fn(*args)

    conv = BrowserConversation(Bridge(), "c1", None)  # type: ignore[arg-type]
    token = current_turn.set(new_turn(("read the page",), "text"))
    try:
        conv._turn()  # the turn begins
        Bridge.service.owner_stops += 1  # the owner pressed Stop in the window
        with pytest.raises(ToolError, match="owner stopped the browser"):
            conv._turn()
    finally:
        current_turn.reset(token)
    token = current_turn.set(new_turn(("try again",), "text"))
    try:
        conv._turn()  # the next turn may start it again
    finally:
        current_turn.reset(token)


def test_state_names_the_bot_using_the_browser(setup: Any) -> None:
    from sasha.conversation import building_for_bot

    client, service, _ = setup
    token = building_for_bot.set("main")  # as make_assistant does while building tools
    try:
        client.connector.build_tools({})
    finally:
        building_for_bot.reset(token)
    holder = next(iter(client.connector.holders))
    view = client.app.state.browser_view

    async def while_held() -> dict[str, Any]:
        async with service.acquire(holder):
            return dict(await view.state())

    state = client.portal.call(while_held)
    main = client.harness.bots.get("main")
    assert state["agent"] == "working" and state["holder"] == holder
    assert state["holder_bot"] == {"id": "main", "name": main.name}
    assert client.get("/api/browser").json()["holder_bot"] is None  # not held now


def test_a_state_loop_error_is_logged_and_the_loop_goes_on(
    setup: Any, caplog: pytest.LogCaptureFixture
) -> None:
    client, service, chrome = setup
    chrome.pipe.attach_failures = 2
    _start(client, service)
    url, window = f"{WS}/ws/browser", {"origin": BASE_URL}
    with client.websocket_connect(url, headers=window) as ws:
        assert ws.receive_json()["running"] is True
        ws.send_json({"type": "browser_watch", "on": True})
        _until(lambda: bool(chrome.pipe.named("Page.startScreencast")))  # third try works
        _frame(client, chrome, 21)
        assert ws.receive_json()["type"] == "browser_frame"
    warnings = [r.message for r in caplog.records if "state loop" in r.message]
    assert warnings == ["Browser view: KeyError in the state loop"] * 2


def test_repeated_errors_close_the_socket_with_1011(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = setup[0]
    calls = {"n": 0}

    async def broken(self: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"running": False}
        raise ValueError("boom")

    monkeypatch.setattr(browser_view.BrowserView, "state", broken)
    url, window = f"{WS}/ws/browser", {"origin": BASE_URL}
    with client.websocket_connect(url, headers=window) as ws:
        assert ws.receive_json()["type"] == "browser_state"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1011 and calls["n"] == 1 + browser_view.MAX_FAILURES
