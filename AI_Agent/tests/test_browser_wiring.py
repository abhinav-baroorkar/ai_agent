"""Browser wiring rules: B4 combination, B1 origin, wake turns, budgets (no Chrome)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from sasha.bots import BotError, BotStore
from sasha.browser import tools as tools_module
from sasha.browser.policy import BrowserCombinationError, check_combination
from sasha.browser.tools import STEPS_PER_TURN, BrowserBridge, BrowserConversation, browser_tools
from sasha.connectors.base import ConnectorError
from sasha.connectors.browser import BrowserConnector, check_template
from sasha.conversation import current_turn, new_turn
from sasha.state import JsonStore
from sasha.tools import ToolError, ToolRegistry


def test_browser_never_shares_a_bot_with_private_data() -> None:
    check_combination({"browser": "browser"})
    check_combination({"home_assistant": "home_assistant", "mcp:notes": "mcp"})
    check_combination({"browser": "browser", "browser:work": "browser"})
    for bad in (
        {"browser": "browser", "home_assistant": "home_assistant"},
        {"browser": "browser", "mcp:notes": "mcp"},
        {"gmail": "gmail", "browser": "browser"},
        # review W1: allowlist on the real kind, so new kinds are refused by default
        {"browser": "browser", "files": "files"},
        {"browser": "browser", "contacts": "contacts"},
        {"browser:work": "browser", "home_assistant": "home_assistant"},
        {"web": "browser", "lights": "home_assistant"},  # ids that aren't their kinds
        {"browser": "browser", "mystery": None},  # unknown kind fails closed
        {"browser:work": None, "home_assistant": "home_assistant"},
    ):
        with pytest.raises(BrowserCombinationError):
            check_combination(bad)


def test_the_bot_store_refuses_browser_plus_home_assistant(tmp_path: Path) -> None:
    store = BotStore(
        JsonStore(tmp_path / "bots.json"),
        main_name="Main",
        main_prompt="p",
        default_model="m",
        connector_ids=lambda: ["home_assistant", "browser", "files", "web"],
        connector_kinds=lambda: {
            "home_assistant": "home_assistant",
            "browser": "browser",
            "files": "files",
            "web": "browser",
        },
        main_connectors=["home_assistant"],
    )
    from sasha.bots import parse_fields

    with pytest.raises(BotError, match="browser"):
        store.update("main", parse_fields({"connectors": ["home_assistant", "browser"]}))
    with pytest.raises(BotError, match="browser"):
        store.create(parse_fields({"name": "Web", "connectors": ["browser", "home_assistant"]}))
    for bad in (["browser", "files"], ["web", "home_assistant"]):
        with pytest.raises(BotError, match="browser"):
            store.create(parse_fields({"name": "Web", "connectors": bad}))
    web = store.create(parse_fields({"name": "Web", "connectors": ["browser"]}))
    assert web.connectors == ["browser"]


def test_tools_for_refuses_the_combination_again_at_run_time(tmp_path: Path) -> None:
    from sasha.connectors import ConnectorRegistry, HomeAssistantConnector

    class FakeBridge(BrowserBridge):
        def __init__(self) -> None:  # no service needed for this check
            pass

    registry = ConnectorRegistry(
        [HomeAssistantConnector.__new__(HomeAssistantConnector), BrowserConnector(FakeBridge())],
        JsonStore(tmp_path / "c.json"),
    )
    tools, warnings = registry.tools_for(["home_assistant", "browser"])
    assert len(tools) == 0 and "browser" in warnings[0]


def test_search_template_setting() -> None:
    assert check_template("") == ""
    assert check_template("https://search.example/?q={q}") == "https://search.example/?q={q}"
    for bad in (
        "http://search.example/?q={q}",
        "https://search.example/{q}",
        "https://search.example/?q={q}&r={q}",
        "https://search.example/?q=x",
    ):
        with pytest.raises(ConnectorError):
            check_template(bad)


# --- the tool rules, with a fake page layer on a real background loop ----------------
class FakeTabs:
    opened: ClassVar[list[str]] = []

    def __init__(self, *args: Any) -> None:
        self.engine_host: str | None = None

    async def navigate(self, url: str, new_tab: bool = False) -> dict[str, Any]:
        FakeTabs.opened.append(url)
        return {"site": "x", "parts": 1, "page": "(part 1 of 1)\nhello"}

    async def read(self, part: int = 1) -> dict[str, Any]:
        return {"site": "x", "parts": 1, "page": "(part 1 of 1)\nhello"}


class FakeService:
    owner_stops = 0

    def begin_turn(self, holder: str) -> None:
        pass

    def end_turn(self, holder: str) -> None:
        pass

    @contextlib.asynccontextmanager
    async def acquire(self, holder: str, owner_stops: int | None = None) -> AsyncIterator[object]:
        yield object()

    async def stop(self) -> None:
        pass


@pytest.fixture
def conv(monkeypatch: pytest.MonkeyPatch) -> Iterator[BrowserConversation]:
    monkeypatch.setattr(tools_module, "BrowserTabs", FakeTabs)
    FakeTabs.opened = []
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    bridge = BrowserBridge(FakeService())  # type: ignore[arg-type]
    loop.call_soon_threadsafe(bridge.attach, loop)
    while bridge.loop is None:
        pass
    yield BrowserConversation(bridge, "c1", "https://search.example/?q={q}")
    loop.call_soon_threadsafe(loop.stop)
    thread.join(2)
    loop.close()


def _in_turn(messages: tuple[str, ...], mode: str = "text") -> Any:
    return current_turn.set(new_turn(messages, mode))


def test_open_url_navigates_only_to_the_rebuilt_owner_address(conv: BrowserConversation) -> None:
    token = _in_turn(("read example.org/news and summarise",))
    try:
        conv.open_url("https://example.org/news#steer-me")
        with pytest.raises(ToolError, match="Only addresses the owner wrote"):
            conv.open_url("https://evil.example/steal")
    finally:
        current_turn.reset(token)
    assert FakeTabs.opened == ["https://example.org/news"]


def test_no_browser_on_wake_turns_or_outside_a_conversation(conv: BrowserConversation) -> None:
    with pytest.raises(ToolError, match="conversation"):
        conv.read_page()
    token = _in_turn(("open example.org",), mode="wake")
    try:
        with pytest.raises(ToolError, match="wake-word"):
            conv.open_url("example.org")
    finally:
        current_turn.reset(token)


def test_step_budget_per_turn(conv: BrowserConversation) -> None:
    token = _in_turn(("hi",))
    try:
        for _ in range(STEPS_PER_TURN):
            conv.read_page()
        with pytest.raises(ToolError, match="step limit"):
            conv.read_page()
    finally:
        current_turn.reset(token)
    token = _in_turn(("next request",))  # a new turn gets a fresh budget
    try:
        conv.read_page()
    finally:
        current_turn.reset(token)


def test_search_needs_an_engine_and_refuses_shortcuts(conv: BrowserConversation) -> None:
    token = _in_turn(("look this up",))
    try:
        conv.search("weather in Pune")
        assert FakeTabs.opened[-1] == "https://search.example/?q=weather+in+Pune"
        with pytest.raises(ToolError, match="redirect"):
            conv.search("pune !w")
        conv.search_template = None
        with pytest.raises(ToolError, match="No search engine is set"):
            conv.search("anything")
    finally:
        current_turn.reset(token)


def test_tools_refuse_to_run_on_the_loop_thread() -> None:
    async def main() -> None:
        bridge = BrowserBridge(FakeService())  # type: ignore[arg-type]
        bridge.attach(asyncio.get_running_loop())

        async def nothing() -> int:
            return 1

        with pytest.raises(ToolError, match="own loop"):
            bridge.run(nothing)

    asyncio.run(main())


def test_the_tool_list_is_read_only() -> None:
    conv = BrowserConversation.__new__(BrowserConversation)
    names = ToolRegistry(browser_tools(conv)).names
    assert names == [
        "search",
        "open_url",
        "read_page",
        "click",
        "scroll",
        "back",
        "list_tabs",
        "switch_tab",
    ]
    assert not {"type_text", "submit", "download"} & set(names)


def test_search_queries_never_carry_personal_identifiers() -> None:
    """Found by the injection set: a page asked for a search with the owner's email."""
    from sasha.browser.leaks import QueryLeakError, check_query

    earlier = ["My email is owner@example.com", "The code word is mango-42", "I like Radiohead"]
    current = "Please read the page and tell me what it says"
    for q in (
        "owner@example.com",
        "contact owner@example.com",
        "call 020 7946 0958",
        "mango-42",
        "the word MANGO-42",
    ):
        with pytest.raises(QueryLeakError):
            check_query(q, earlier, current)
    for q in ("Radiohead tour dates", "weather in Pune", "python 3.10 release", "mango recipes"):
        check_query(q, earlier, current)
    check_query("track AB12CD", ["ship AB12CD"], "where is parcel AB12CD now?")  # asked now


def test_leak_check_sees_through_rewritten_identifiers() -> None:
    """Review W2: each of these passed the first version."""
    from sasha.browser.leaks import QueryLeakError, check_query

    earlier = ["mail pat.doe@example.com, code mango-42, call 555 123 4567, parcel AB12CD"]
    current = "Please read the page"
    for q in (
        "pat.doe at example dot com",
        "pat.doe\uff20example.com",  # full-width at sign
        "pat.doe%40example.com",
        "pat . doe @ example . com",
        "five five five one two three four",
        "mango 42",
        "mango42",
        "42-mango",
        "m-a-n-g-o-4-2",
        "mango four two",
        "MANGO four-two",
        "AB 12 CD",
        "ab-12-cd tracking",
    ):
        with pytest.raises(QueryLeakError):
            check_query(q, earlier, current)
    check_query("555 123", earlier, current)  # alone it's fine...
    with pytest.raises(QueryLeakError):  # ...but not split across the turn's searches
        check_query("4567", earlier, current, before=["555 123"])
    with pytest.raises(QueryLeakError):
        check_query("42 facts", earlier, current, before=["mango"])
    for q in (
        "tutorials at python dot org",
        "restaurants at pune",
        "mango recipes",
        "top 10 films of 2024",
        "one direction tour",
    ):
        check_query(q, earlier, current)


def test_the_turns_searches_are_checked_together(conv: BrowserConversation) -> None:
    token = _in_turn(("the code is mango-42", "read the page"))
    try:
        conv.search("mango")
        with pytest.raises(ToolError, match="earlier messages"):
            conv.search("42")
    finally:
        current_turn.reset(token)


def test_search_tool_refuses_a_leaky_query(conv: BrowserConversation) -> None:
    token = _in_turn(("my email is owner@example.com", "read the page"))
    try:
        with pytest.raises(ToolError, match="email"):
            conv.search("owner@example.com")
    finally:
        current_turn.reset(token)
