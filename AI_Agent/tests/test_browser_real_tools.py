"""The browser tools end to end on real headless Chrome; pages on test.invalid, no network."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest

from sasha.browser.chrome import find_chrome
from sasha.browser.fixtures import serve_pages
from sasha.browser.proxy import FilteringProxy
from sasha.browser.service import BrowserService
from sasha.browser.tools import BrowserBridge, BrowserConversation
from sasha.conversation import current_turn, new_turn
from sasha.tools import ToolError

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="no Chrome installed")
T = TypeVar("T")

PAGES = {
    "/home": """<title>Home page</title><h1>Welcome</h1><p>The answer is 42.</p>
        <a href="/next">Next page</a> <a href="javascript:alert(1)">Script link</a>
        <a href="http://127.0.0.1:8765/api/health">Brain</a> <button>Do something</button>""",
    "/next": "<title>Second</title><h1>Page two</h1><p>Pune is in Maharashtra.</p>",
    "/other": "<title>Other</title><p>Another tab.</p>",
    # a results page like DuckDuckGo's HTML one: links go through /l/?uddg=<real address>
    "/search": """<title>Results</title>
        <a href="/l/?uddg=https%3A%2F%2Ftest.invalid%2Fnext&rut=1">First result</a>
        <a href="//test.invalid/l/?uddg=http%3A%2F%2F127.0.0.1%3A8765%2Fapi%2Fhealth">Local</a>""",
}


async def _no_dns(host: str, port: int) -> list[Any]:
    raise OSError("no DNS in tests")


def test_tools_on_real_chrome(tmp_path: Path) -> None:
    owner = ("please read https://test.invalid/home and also test.invalid/other",)

    async def main() -> None:
        service = BrowserService(tmp_path, proxy_factory=lambda: FilteringProxy(_no_dns))
        bridge = BrowserBridge(service, on_tab=serve_pages(PAGES))
        bridge.attach(asyncio.get_running_loop())
        conv = BrowserConversation(bridge, "c1", None)

        async def tool(call: Callable[[], T]) -> T:
            def in_turn() -> T:
                token = current_turn.set(new_turn(owner, "text"))
                try:
                    return call()
                finally:
                    current_turn.reset(token)

            return await asyncio.to_thread(in_turn)

        try:
            home = await tool(lambda: conv.open_url("https://test.invalid/home"))
            page = home["page"]
            assert home["site"] == "test.invalid" and "The answer is 42." in page
            assert "note" in home and "never instructions" in home["note"]
            number = {
                line.split('"')[1]: int(line[1 : line.index("]")])
                for line in page.splitlines()
                if line.startswith("[")
            }
            nxt = await tool(lambda: conv.click(number["Next page"]))
            assert "Pune is in Maharashtra." in nxt["page"]
            back = await tool(lambda: conv.back())
            assert "The answer is 42." in back["page"]
            for name, why in (
                ("Script link", "doesn't lead to a web page"),
                ("Do something", "only links"),
            ):
                with pytest.raises(ToolError, match=why):
                    await tool(lambda n=name: conv.click(number[n]))
            with pytest.raises(ToolError, match="isn't allowed"):  # the guard, via the proxy
                await tool(lambda: conv.click(number["Brain"]))
            back_home = await tool(lambda: conv.back())  # the brain link left an error page
            assert "The answer is 42." in back_home["page"]
            other = await tool(lambda: conv.open_url("test.invalid/other", new_tab=True))
            assert "Another tab." in other["page"]
            tabs = await tool(lambda: conv.list_tabs())
            assert [t["tab"] for t in tabs["tabs"]] == [1, 2] and tabs["tabs"][1]["current"]
            first = await tool(lambda: conv.switch_tab(1))
            assert first["site"] == "test.invalid"
            assert bridge.backstop.counts.clicks >= 2  # B8 counts, no addresses
            # search-engine redirects: the model sees, and the click opens, the destination
            conv.search_template = "https://test.invalid/search?q={q}"
            results = await tool(lambda: conv.search("pune facts"))
            lines = {ln.split('"')[1]: ln for ln in results["page"].splitlines() if ln[:1] == "["}
            assert lines["First result"].endswith("→ test.invalid/next"), lines
            assert lines["Local"].endswith("→ 127.0.0.1/api/health"), lines
            assert "uddg" not in results["page"]
            pick = {k: int(v[1 : v.index("]")]) for k, v in lines.items()}
            clicks_before = bridge.backstop.counts.clicks
            first_hit = await tool(lambda: conv.click(pick["First result"]))
            assert "Pune is in Maharashtra." in first_hit["page"]
            await tool(lambda: conv.back())
            with pytest.raises(ToolError, match="isn't allowed"):  # refused at the final hop
                await tool(lambda: conv.click(pick["Local"]))
            assert bridge.backstop.counts.clicks == clicks_before + 2
            assert service.proxy is not None and service.proxy.stats.allowed == 0
        finally:
            await bridge.stop()
        assert service.chrome is None
        # the stop forgot the browsing history: the search query is nowhere in the profile
        profile = tmp_path / "browser-profile"
        assert not (profile / "Default" / "History").exists()
        for f in profile.rglob("*"):
            if f.is_file() and not f.is_symlink():
                assert b"facts" not in f.read_bytes(), f.relative_to(profile)

    asyncio.run(main())


def test_live_view_frames_of_a_test_page_arrive(tmp_path: Path) -> None:
    """Contract v0.4 on real Chrome: the current tab's screencast and snapshot are JPEGs."""
    from sasha.browser.backstop import LogOnlyBackstop
    from sasha.browser.pages import BrowserTabs
    from sasha.browser.screencast import Screencast, is_jpeg, snapshot

    async def main() -> None:
        service = BrowserService(tmp_path, proxy_factory=lambda: FilteringProxy(_no_dns))
        frames: list[dict[str, Any]] = []
        try:
            async with service.acquire("c1") as chrome:
                tabs = BrowserTabs(chrome, LogOnlyBackstop(), serve_pages(PAGES))
                await tabs.navigate("https://test.invalid/home")
                assert chrome.current_target is not None and chrome.tab_targets == [
                    chrome.current_target
                ]

                async def emit(message: dict[str, Any]) -> None:
                    frames.append(message)

                cast = Screencast(chrome.pipe, chrome.current_target, 1, emit)
                await cast.start()
                for _ in range(100):
                    if frames:
                        break
                    await asyncio.sleep(0.05)
                await cast.window_ack(frames[0]["seq"] if frames else -1)
                await cast.stop()
                jpeg = await snapshot(chrome.pipe, chrome.current_target)
        finally:
            await service.stop()
        assert frames, "no screencast frame arrived"
        first = frames[0]
        assert first["type"] == "browser_frame" and first["tab"] == 1 and first["w"] > 0
        assert is_jpeg(first["jpeg"]) and jpeg[:3] == b"\xff\xd8\xff"

    asyncio.run(main())
