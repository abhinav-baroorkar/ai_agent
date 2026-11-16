"""The owner's take-over (browser-design.md §9, C1-C4, S1-S2) with fakes: no window."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import sasha.browser.takeover as takeover_module
from sasha.browser.cookies import keep_only_site_data
from sasha.browser.profile import prepare_profile
from sasha.browser.proxy import FilteringProxy
from sasha.browser.service import DRIVING, BrowserBusyError, BrowserService
from sasha.browser.takeover import TakeOver
from sasha.browser.takeover_files import (
    DOWNLOADS,
    TakeoverRefused,
    clear_extensions,
    engine_home,
    extensions_present,
    restore_config,
    seed_takeover_preferences,
    snapshot_config,
    visible_flags,
)
from tests.test_browser_view import FakeChrome

TEMPLATE = "https://html.duckduckgo.com/html/?q={q}"


def test_the_visible_window_has_no_devtools_and_goes_through_the_guard(tmp_path: Path) -> None:
    flags = visible_flags(tmp_path, 4242, engine_home(TEMPLATE))
    assert not any(f.startswith(("--remote-debugging", "--headless")) for f in flags)
    for must in (
        "--disable-extensions",
        "--disable-sync",
        "--disable-background-mode",
        "--proxy-server=http://127.0.0.1:4242",
        "--proxy-bypass-list=<-loopback>",
        "--deny-permission-prompts",
    ):
        assert must in flags, must
    assert flags[-1] == "https://html.duckduckgo.com/"  # the engine's home, never a query
    assert sum(f.startswith("--disable-features=") for f in flags) == 1
    assert engine_home("http://plain.example/?q={q}") == "about:blank"
    assert engine_home(None) == "about:blank"


def test_c1_nothing_lasting_from_his_window(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    (root / "Default").mkdir()
    (root / "Default" / "Preferences").write_text('{"ours": 1}')
    (root / "Local State").write_text('{"flags": []}')
    snapshot_config(root, tmp_path / "keep")
    (root / "Default" / "Preferences").write_text('{"his": "password saving on"}')
    (root / "Local State").write_text('{"flags": ["something"]}')
    (root / "Default" / "Secure Preferences").write_text("{}")  # new during the take-over
    (root / "Default" / "Extensions" / "abc").mkdir(parents=True)
    assert extensions_present(root)
    restore_config(root, tmp_path / "keep")
    clear_extensions(root)
    assert json.loads((root / "Default" / "Preferences").read_text()) == {"ours": 1}
    assert json.loads((root / "Local State").read_text()) == {"flags": []}
    assert not (root / "Default" / "Secure Preferences").exists()
    assert not extensions_present(root) and not (tmp_path / "keep").exists()


def test_the_takeover_preferences_block_permissions_and_saving(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    seed_takeover_preferences(root)
    prefs = json.loads((root / "Default" / "Preferences").read_text())
    blocked = prefs["profile"]["default_content_setting_values"]
    assert blocked["media_stream_mic"] == 2 and blocked["notifications"] == 2
    assert prefs["profile"]["password_manager_enabled"] is False
    assert prefs["credentials_enable_service"] is False
    assert prefs["autofill"] == {"profile_enabled": False, "credit_card_enabled": False}
    assert prefs["download"]["default_directory"] == str(root / DOWNLOADS)
    (root / "Default" / "Preferences").unlink()
    (root / "Default" / "Preferences").mkdir()  # in the way: refused, not ignored
    with pytest.raises(TakeoverRefused):
        seed_takeover_preferences(root)


class CookiePipe:
    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self.cookies, self.calls = cookies, []  # type: ignore[var-annotated]

    async def call(self, method: str, params: dict[str, Any] | None = None, **_: Any) -> Any:
        self.calls.append((method, params))
        return {"cookies": self.cookies} if method == "Storage.getCookies" else {}


def test_c3_only_the_engines_cookies_are_kept() -> None:
    pipe = CookiePipe(
        [
            {
                "name": "check",
                "value": "1",
                "domain": ".duckduckgo.com",
                "path": "/",
                "expires": -1,
            },
            {"name": "sid", "value": "x", "domain": "accounts.example.com", "path": "/"},
            {"name": "t", "value": "y", "domain": ".tracker.example", "path": "/"},
        ]
    )

    class C:
        pass

    chrome = C()
    chrome.pipe = pipe  # type: ignore[attr-defined]
    forgotten = asyncio.run(keep_only_site_data(chrome, "duckduckgo.com"))
    methods = [m for m, _ in pipe.calls]
    assert methods[:3] == ["Storage.getCookies", "Storage.clearCookies", "Storage.setCookies"]
    kept = pipe.calls[2][1]["cookies"]  # type: ignore[index]
    assert [c["name"] for c in kept] == ["check"] and "expires" not in kept[0]
    origins = {p["origin"] for m, p in pipe.calls if m == "Storage.clearDataForOrigin"}  # type: ignore[index]
    assert origins == {f"{s}://{h}" for s in ("http", "https")
                       for h in ("accounts.example.com", "tracker.example")}  # fmt: skip
    assert forgotten == 2


class FakeProc:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.closed = asyncio.Event()
        self.signals: list[int] = []

    async def wait(self) -> int:
        await self.closed.wait()
        self.returncode = 0
        return 0

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.closed.set()

    def kill(self) -> None:
        self.closed.set()


async def _until(check: Any, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


def _rig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[BrowserService, TakeOver, list[Any]]:
    async def starter(data_dir: Path, port: int) -> Any:
        return FakeChrome()

    monkeypatch.setattr(takeover_module, "find_chrome", lambda: "/usr/bin/chromium")
    service = BrowserService(tmp_path, starter=starter, proxy_factory=FilteringProxy)
    spawned: list[Any] = []

    async def spawn(*argv: str, **kw: Any) -> FakeProc:
        proc = FakeProc()
        spawned.append((list(argv), kw, proc))
        return proc

    t = TakeOver(service, lambda: TEMPLATE, spawn=spawn, has_display=lambda: True)
    return service, t, spawned


def test_take_over_waits_for_the_turn_then_hands_back_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def main() -> None:
        service, t, spawned = _rig(tmp_path, monkeypatch)
        async with service.acquire("conversation-a", turn=True):
            pass  # a turn holds the browser
        await t.start()
        assert t.mode == "waiting" and service.driving
        with pytest.raises(BrowserBusyError, match="owner is using the browser"):
            async with service.acquire("conversation-b", turn=True):
                pass
        await asyncio.sleep(0.3)
        assert t.mode == "waiting" and not spawned  # still waiting for A's turn
        service.end_turn("conversation-a")
        await _until(lambda: t.mode == "owner")
        argv = spawned[0][0]
        assert "--disable-extensions" in argv and not any("remote-debugging" in a for a in argv)
        assert argv[-1] == "https://html.duckduckgo.com/" and t.state()["owner_until"]
        root = prepare_profile(tmp_path)
        (root / "Default" / "Extensions" / "x").mkdir(parents=True)  # he installs one
        spawned[0][2].closed.set()  # he closes the window
        await _until(lambda: t.mode == "assistant")
        assert not service.driving and not extensions_present(root)
        assert service.keep_cookies_of == "duckduckgo.com"  # C3 at the next start
        assert not (root / DOWNLOADS).exists()
        await service.stop()

    asyncio.run(main())


def test_stop_it_now_and_the_time_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(takeover_module, "MAX_OWNER_S", 0.3)

    async def main() -> None:
        service, t, spawned = _rig(tmp_path, monkeypatch)
        async with service.acquire("conversation-a", turn=True):
            pass
        stops = service.owner_stops
        await t.start()
        t.stop_turn()  # "Stop it now"
        assert service.owner_stops == stops + 1
        await _until(lambda: t.mode == "owner")
        await _until(lambda: t.mode == "assistant")  # the limit hands back by itself
        assert spawned[0][2].signals  # SIGTERM to the window
        await service.stop()

    asyncio.run(main())


def test_a_failed_start_goes_straight_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S2: never left in owner mode, never with a half-made profile."""

    async def main() -> None:
        service, t, _ = _rig(tmp_path, monkeypatch)

        async def broken(*argv: str, **kw: Any) -> Any:
            raise OSError("no display after all")

        t.spawn = broken
        await t.start()
        await _until(lambda: t.mode == "assistant")
        assert not service.driving and not (tmp_path / "browser-takeover-config").exists()
        await service.stop()

    asyncio.run(main())


def test_refused_without_a_screen_or_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def main() -> None:
        service, t, _ = _rig(tmp_path, monkeypatch)
        t.has_display = lambda: False
        with pytest.raises(TakeoverRefused, match="No screen"):
            await t.start()
        assert not service.driving
        t.has_display = lambda: True
        await t.start()
        with pytest.raises(TakeoverRefused, match="already"):
            await t.start()
        await _until(lambda: t.mode == "owner")
        await t.hand_back()
        assert t.mode == "assistant" and DRIVING.startswith("The owner")
        await service.stop()

    asyncio.run(main())


def test_the_service_refuses_extensions_and_filters_cookies_at_the_next_start(
    tmp_path: Path,
) -> None:
    from sasha.browser.chrome import BrowserUnavailableError

    chrome = FakeChrome()
    filtered: list[str] = []

    async def starter(data_dir: Path, port: int) -> Any:
        return chrome

    async def main() -> None:
        import sasha.browser.service as service_module

        async def fake_keep(c: Any, site: str) -> int:
            filtered.append(site)
            return 0

        service_module.keep_only_site_data = fake_keep  # type: ignore[assignment]
        service = BrowserService(tmp_path, starter=starter, proxy_factory=FilteringProxy)
        root = prepare_profile(tmp_path)
        (root / "Default" / "Extensions" / "x").mkdir(parents=True)
        with pytest.raises(BrowserUnavailableError, match="Extensions"):
            async with service.acquire("c"):
                pass
        clear_extensions(root)
        service.keep_cookies_of = "duckduckgo.com"
        async with service.acquire("c"):
            pass
        assert filtered == ["duckduckgo.com"] and service.keep_cookies_of is None
        await service.stop()

    import sasha.browser.service as service_module

    real = service_module.keep_only_site_data
    try:
        asyncio.run(main())
    finally:
        service_module.keep_only_site_data = real


def test_the_takeover_routes_need_the_token_and_the_browser_on(
    settings: Any, tmp_path: Path
) -> None:
    from sasha.browser.tools import BrowserBridge
    from sasha.connectors import ConnectorRegistry
    from sasha.connectors.browser import BrowserConnector
    from sasha.server.app import create_app
    from sasha.server.window_token import WindowToken
    from sasha.state import JsonStore
    from tests.server_fakes import BASE_URL, Harness
    from tests.test_browser_view import LOCAL, TOKEN, WindowClient

    harness = Harness(settings, tmp_path)
    service = BrowserService(tmp_path, proxy_factory=FilteringProxy)
    harness.deps.connectors = ConnectorRegistry(
        [BrowserConnector(BrowserBridge(service))], JsonStore(tmp_path / "c.json")
    )
    harness.deps.window_token = WindowToken(TOKEN)
    app = create_app(harness.deps, web_dir=None)
    client = WindowClient(app, base_url=BASE_URL, client=LOCAL)
    from fastapi.testclient import TestClient

    bare = TestClient(app, base_url=BASE_URL, client=LOCAL)
    for path in ("take_over", "hand_back", "stop_turn"):
        assert bare.post(f"/api/browser/{path}").status_code == 401
    off = client.post("/api/browser/take_over")
    assert off.status_code == 409 and "off in Settings" in off.json()["error"]
    assert client.post("/api/browser/hand_back").json() == {"ok": True}  # nothing to hand back
    state = client.get("/api/browser").json()
    assert state["mode"] == "assistant" and state["owner_until"] is None
    assert state["owner_refused"] == {}
