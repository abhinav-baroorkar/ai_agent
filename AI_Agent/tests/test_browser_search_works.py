"""Search that works out of the box (owner: "I need Sasha to access the browser"):
the default engine, the built-in bots on web lookups, an address the owner named is
opened (not searched), and a person check is said plainly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sasha.bots import MAIN_ID, VOICE_ID, BotError, BotStore, parse_fields
from sasha.browser.helper import OPEN_FIRST, BrowseHelper, named_address
from sasha.browser.pages import NOT_RESULTS, PERSON_CHECK, asks_for_a_person
from sasha.browser.tools import BrowserConversation
from sasha.config import Settings
from sasha.connectors.browser import DEFAULT_SEARCH, BrowserConnector
from sasha.conversation import current_turn, new_turn
from sasha.pipeline.llm import ToolCall
from sasha.prompting import prompt_lines
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter
from tests.server_fakes import FakeLLM, resp
from tests.test_browse_helper import conv  # noqa: F401  (fixture)


def test_an_empty_or_missing_engine_is_the_default() -> None:
    connector = BrowserConnector(object())  # type: ignore[arg-type]
    assert connector.validate_settings({}) == {"search_template": DEFAULT_SEARCH}
    assert connector.validate_settings({"search_template": ""}) == {
        "search_template": DEFAULT_SEARCH
    }
    other = "https://search.example/?q={q}"
    assert connector.validate_settings({"search_template": other}) == {"search_template": other}


def _store(tmp_path: Path, saved: dict[str, Any]) -> BotStore:
    path = tmp_path / "bots.json"
    path.write_text(json.dumps(saved))
    return BotStore(
        JsonStore(path),
        main_name="Main",
        main_prompt="p",
        default_model="m",
        connector_ids=lambda: ["home_assistant", "browser"],
        connector_kinds=lambda: {"home_assistant": "home_assistant", "browser": "browser"},
        main_connectors=["home_assistant"],
    )


def test_the_built_in_bots_never_keep_the_direct_browser(tmp_path: Path) -> None:
    store = _store(tmp_path, {MAIN_ID: {"connectors": ["browser"]},
                              VOICE_ID: {"connectors": ["browser"]}})  # fmt: skip
    assert store.get(MAIN_ID).connectors == ["home_assistant"]  # migrated on read
    assert store.get(VOICE_ID).connectors == ["home_assistant"]
    with pytest.raises(BotError, match="web lookups"):
        store.update(MAIN_ID, parse_fields({"connectors": ["browser"]}))
    web = store.create(parse_fields({"name": "Web", "connectors": ["browser"]}))
    assert store.get(web.id).connectors == ["browser"]  # a separate bot still may


def test_a_person_check_is_told_apart_from_no_results() -> None:
    ddg = (
        "Unfortunately, bots use DuckDuckGo too. Please complete the following challenge "
        "to confirm this search was made by a human."
    )
    assert asks_for_a_person(ddg)
    assert asks_for_a_person("Please confirm you are a person.")
    assert not asks_for_a_person("Search results for pune weather")
    assert "Take over" in PERSON_CHECK and "Take over" in NOT_RESULTS


def test_an_address_the_owner_named_is_opened_first(
    settings: Settings,
    tmp_path: Path,
    conv: BrowserConversation,  # noqa: F811
) -> None:
    said = "Look up example.com on the web and tell me the heading"
    assert named_address("example.com heading", said) == "https://example.com/"
    assert named_address("weather today", "what's the weather today") is None
    llm = FakeLLM("helper")
    llm.responses = [resp(calls=[ToolCall("open_url", {"url": "https://example.com/"})]),
                     resp(json.dumps({"answer": "Example Domain", "facts": []}))]  # fmt: skip
    helper = BrowseHelper(conv, settings, llm, TelemetryWriter(tmp_path, "a", "p", enabled=False),
                          prompt_lines(settings))  # fmt: skip
    token = current_turn.set(new_turn((said,), "text"))
    try:
        out = helper.browse("example.com heading")
    finally:
        current_turn.reset(token)
    task = llm.seen[0][0][1]["content"]
    assert OPEN_FIRST.format(url="https://example.com/") in task
    assert out["answer"] == "Example Domain" and out["sites"] == ["x"]


def test_a_blocked_search_says_so_plainly(
    settings: Settings,
    tmp_path: Path,
    conv: BrowserConversation,  # noqa: F811
) -> None:
    llm = FakeLLM("helper")
    nothing = json.dumps({"answer": "The search didn't return any results."})
    llm.responses = [resp(calls=[ToolCall("search", {"q": "pune weather"})]), resp(nothing)]
    helper = BrowseHelper(conv, settings, llm, TelemetryWriter(tmp_path, "a", "p", enabled=False),
                          prompt_lines(settings))  # fmt: skip

    async def blocked(url: str) -> Any:
        from sasha.browser.pages import PageError

        raise PageError(PERSON_CHECK)

    import tests.test_browser_wiring as wiring

    original = wiring.FakeTabs.search_results
    wiring.FakeTabs.search_results = lambda self, url: blocked(url)  # type: ignore[method-assign]
    from sasha.browser.helper import LookupFailed

    token = current_turn.set(new_turn(("pune weather",), "text"))
    try:
        with pytest.raises(LookupFailed) as failed:
            helper.browse("pune weather")
    finally:
        current_turn.reset(token)
        wiring.FakeTabs.search_results = original  # type: ignore[method-assign]
    assert failed.value.asks == "person_check" and PERSON_CHECK in str(failed.value)
