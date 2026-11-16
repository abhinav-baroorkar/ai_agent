"""Page operations for one conversation, run on the brain's event loop (phase 1: read).

Tabs belong to the conversation. Everything here is read-only: navigate to an address
the server already vetted, read the page as text, follow a numbered link (by going to
its http(s) address, so no page script runs for a click), scroll, go back, switch tabs.
Results are plain data for the tool layer; nothing here logs page text or addresses.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from sasha.browser.backstop import LogOnlyBackstop
from sasha.browser.cdp import CDPError, CDPPipe
from sasha.browser.chrome import Chrome, new_page
from sasha.browser.reader import PageView, clean, read_tree
from sasha.browser.redirects import looks_like_results, site_of, unwrap

LOAD_TIMEOUT_S = 20.0
NOT_RESULTS = (
    "No results, or the search engine is asking the owner to confirm a person is "
    "searching: ask the owner to take over the browser; don't try anything else."
)
MAX_PAGE_HOSTS = 4096  # hosts remembered per run; refusals of hosts first seen after
# the cap count as background (harmless: it only shifts the live view's counts)
MAX_DESTINATIONS = 150  # links whose destination is looked up per read
MAX_TABS = 3
TabHook = Callable[[CDPPipe, str], Awaitable[None]]  # tests serve pages via Fetch


class PageError(RuntimeError):
    """A page operation failed; the message is safe to show the model."""


@dataclass
class Tab:
    target_id: str
    session_id: str
    loaded: asyncio.Event
    view: PageView | None = None
    destinations: dict[int, str] = field(default_factory=dict)  # link number -> real URL
    unwrapped_links: int = 0  # links unwrapped from the engine's result redirect
    offsite_links: int = 0  # links leading off the engine's site


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class BrowserTabs:
    def __init__(
        self,
        chrome: Chrome,
        backstop: LogOnlyBackstop,
        on_tab: TabHook | None = None,
        engine_host: str | None = None,
    ) -> None:
        self.chrome = chrome
        self.backstop = backstop
        self.on_tab = on_tab
        self.engine_host = engine_host  # result links on this site are unwrapped
        self.tabs: list[Tab] = []
        self._current = -1
        chrome.pipe.on("Page.loadEventFired", self._loaded)
        chrome.pipe.on("Network.requestWillBeSent", self._request)

    @property
    def current(self) -> int:
        return self._current

    @current.setter
    def current(self, index: int) -> None:
        """Also tells the live view which tabs exist and which one to show."""
        self._current = index
        self.chrome.tab_targets = [t.target_id for t in self.tabs]
        ok = 0 <= index < len(self.tabs)
        self.chrome.current_target = self.tabs[index].target_id if ok else None

    @property
    def pipe(self) -> CDPPipe:
        return self.chrome.pipe

    async def _call(self, method: str, params: dict[str, Any] | None, tab: Tab) -> dict[str, Any]:
        """A page call that rides out Chrome swapping the page's renderer (for example to
        its error page): such a call briefly answers "Not attached", so retry ~1 s."""
        for attempt in range(10):
            try:
                return await self.pipe.call(method, params, session_id=tab.session_id)
            except CDPError as e:
                if "Not attached" not in str(e) or attempt == 9:
                    raise
                await asyncio.sleep(0.1)
        raise AssertionError("unreachable")

    def _tab_for(self, session: Any) -> Tab | None:
        return next((t for t in self.tabs if t.session_id == session), None)

    def _loaded(self, params: dict[str, Any]) -> None:
        if (tab := self._tab_for(params.get("_session"))) is not None:
            tab.loaded.set()

    def _request(self, params: dict[str, Any]) -> None:
        if self._tab_for(params.get("_session")) is not None:
            self.backstop.on_request_will_be_sent(params)  # methods only (B8)
            host = domain_of(str(params.get("request", {}).get("url", ""))).rstrip(".")
            if host and len(self.chrome.page_hosts) < MAX_PAGE_HOSTS:
                self.chrome.page_hosts.add(host)

    async def _new_tab(self) -> Tab:
        if len(self.tabs) >= MAX_TABS:
            raise PageError(f"At most {MAX_TABS} tabs; switch to one or go back")
        target_id, session = await new_page(self.chrome)
        tab = Tab(target_id, session, asyncio.Event())
        self.tabs.append(tab)
        for method in ("Page.enable", "Network.enable", "Accessibility.enable"):
            await self.pipe.call(method, session_id=session)
        if self.on_tab is not None:
            await self.on_tab(self.pipe, session)
        self.current = len(self.tabs) - 1
        return tab

    async def tab(self) -> Tab:
        if self.current < 0:
            return await self._new_tab()
        return self.tabs[self.current]

    async def navigate(self, url: str, new_tab: bool = False) -> dict[str, Any]:
        """Open an address the server has already checked (`verdict.url`)."""
        tab = await self._new_tab() if new_tab else await self.tab()
        tab.loaded.clear()
        tab.view = None
        try:
            result = await self._call("Page.navigate", {"url": url}, tab)
        except CDPError as e:
            raise PageError("The page could not be opened") from e
        if error := result.get("errorText"):
            raise PageError(_plain_error(str(error)))
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(tab.loaded.wait(), LOAD_TIMEOUT_S)
        return await self.read(1)

    async def read(self, part: int = 1) -> dict[str, Any]:
        tab = await self.tab()
        here = await self._evaluate(tab, "location.href")
        if tab.view is None or part == 1:
            title = await self._evaluate(tab, "document.title")
            tree = await self._call("Accessibility.getFullAXTree", None, tab)
            nodes = tree.get("nodes", [])
            first = read_tree(nodes, title=str(title or ""))
            tab.unwrapped_links = tab.offsite_links = 0
            by_node = await self._destinations(tab, first, str(here or ""))
            tab.view = read_tree(nodes, title=str(title or ""), destinations=by_node)
            tab.destinations = {
                n: by_node[e.backend_node_id]
                for n, e in tab.view.elements.items()
                if e.backend_node_id is not None and e.backend_node_id in by_node
            }
        return {
            "site": domain_of(str(here or "")),
            "parts": len(tab.view.parts),
            "page": tab.view.part(part),
        }

    async def follow(self, number: int) -> dict[str, Any]:
        """Phase 1 clicks links only, by opening their http(s) address."""
        tab = await self.tab()
        if tab.view is None:
            raise PageError("Read the page first, then use a number from it")
        element = tab.view.elements.get(number)
        if element is None:
            raise PageError(f"No element [{number}] on this page")
        if element.role != "link" or element.backend_node_id is None:
            raise PageError(f"[{number}] is a {element.role}; only links can be used for now")
        href = tab.destinations.get(number) or unwrap(
            await self._href(tab, element.backend_node_id), "", self.engine_host
        )
        if urlsplit(href).scheme not in ("http", "https"):
            raise PageError(f"[{number}] doesn't lead to a web page")
        self.backstop.click()
        return await self.navigate(href)

    async def _destinations(self, tab: Tab, view: PageView, base: str) -> dict[int, str]:
        """Where each link (the first MAX_DESTINATIONS) really leads: its href attribute,
        resolved and unwrapped from the search engine's redirect. One call per link."""
        found: dict[int, str] = {}
        links = [e for e in view.elements.values() if e.role == "link" and e.backend_node_id]
        for element in links[:MAX_DESTINATIONS]:
            node_id = element.backend_node_id
            assert node_id is not None
            try:
                info = await self._call("DOM.describeNode", {"backendNodeId": node_id}, tab)
            except CDPError:
                continue
            attributes = info.get("node", {}).get("attributes", [])
            pairs = dict(zip(attributes[::2], attributes[1::2], strict=False))
            href = pairs.get("href")
            if href:
                target = unwrap(str(href), base, self.engine_host)
                if urlsplit(target).scheme in ("http", "https"):
                    found[node_id] = target
                    if target != urljoin(base, str(href)):
                        tab.unwrapped_links += 1
                    site = site_of(urlsplit(target).hostname or "")
                    if self.engine_host and site != site_of(self.engine_host):
                        tab.offsite_links += 1
        return found

    async def search_results(self, url: str) -> dict[str, Any]:
        """Open a results page built by `search_url`; refuse one with no result links
        (a bot challenge, so far): the model is told, and never reads that page."""
        result = await self.navigate(url)
        tab = await self.tab()
        if not looks_like_results(self.engine_host, tab.unwrapped_links, tab.offsite_links):
            raise PageError(NOT_RESULTS)
        return result

    async def _href(self, tab: Tab, backend_node_id: int) -> str:
        try:
            node = await self._call("DOM.resolveNode", {"backendNodeId": backend_node_id}, tab)
            result = await self._call(
                "Runtime.callFunctionOn",
                {
                    "objectId": node["object"]["objectId"],
                    "returnByValue": True,
                    "functionDeclaration": "function(){return String(this.href||'')}",
                },
                tab,
            )
        except (CDPError, KeyError) as e:
            raise PageError("That link is no longer on the page") from e
        return str(result.get("result", {}).get("value", ""))

    async def scroll(self, direction: str) -> dict[str, Any]:
        tab = await self.tab()
        delta = -600 if direction == "up" else 600
        await self._call(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": 200, "y": 200, "deltaX": 0, "deltaY": delta},
            tab,
        )
        return {"scrolled": direction}

    async def back(self) -> dict[str, Any]:
        tab = await self.tab()
        history = await self._call("Page.getNavigationHistory", None, tab)
        index = int(history.get("currentIndex", 0))
        if index <= 0:
            raise PageError("There is no earlier page in this tab")
        tab.loaded.clear()
        entry = history["entries"][index - 1]["id"]
        await self._call("Page.navigateToHistoryEntry", {"entryId": entry}, tab)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(tab.loaded.wait(), LOAD_TIMEOUT_S)
        return await self.read(1)

    async def list_tabs(self) -> dict[str, Any]:
        rows = []
        for n, tab in enumerate(self.tabs, 1):
            title = clean(str(await self._evaluate(tab, "document.title") or ""), 80)
            site = domain_of(str(await self._evaluate(tab, "location.href") or ""))
            rows.append({"tab": n, "site": site, "title": title, "current": n - 1 == self.current})
        return {"tabs": rows}

    async def switch(self, number: int) -> dict[str, Any]:
        if not 1 <= number <= len(self.tabs):
            raise PageError(f"No tab {number}; there are {len(self.tabs)}")
        self.current = number - 1
        return await self.read(1)

    async def close(self) -> None:
        for tab in self.tabs:
            with contextlib.suppress(Exception):
                await self.pipe.call("Target.closeTarget", {"targetId": tab.target_id})
        self.tabs.clear()
        self.current = -1

    async def _evaluate(self, tab: Tab, expression: str) -> Any:
        try:
            result = await self._call(
                "Runtime.evaluate", {"expression": expression, "returnByValue": True}, tab
            )
        except CDPError:
            return None
        return result.get("result", {}).get("value")


def _plain_error(error_text: str) -> str:
    if "TUNNEL_CONNECTION_FAILED" in error_text or "RESPONSE_CODE_FAILURE" in error_text:
        return "That address isn't allowed or couldn't be reached"
    if "NAME_NOT_RESOLVED" in error_text:
        return "That site name couldn't be found"
    return "The page could not be opened"
