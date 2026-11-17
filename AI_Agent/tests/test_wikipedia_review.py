"""Review of feat/wikipedia-lookup 593a212: F1 (the main assistant's topic in the owner's
own words), W2 (the turn's queries checked together, searches too), and the nits
(dot keys, the wake label, "From Wikipedia." said by the server). Fakes only."""

from __future__ import annotations

from typing import Any

import pytest

from sasha.bots import MAIN_ID
from sasha.browser import tools as tools_module
from sasha.browser.tools import BrowserConversation
from sasha.browser.wikipedia import WIKI_TOOL, WikipediaLookup
from sasha.config import Settings
from sasha.config_wikipedia import WikipediaConfig
from sasha.conversation import current_turn, new_turn
from sasha.prompting import prompt_lines
from sasha.server.browse_truth import BrowseTruth
from sasha.server.spoken_cap import LookupCap
from sasha.tools import ToolError
from tests.test_browse_taint import harness  # noqa: F401  (the fixture)
from tests.test_browser_wiring import FakeTabs, conv  # noqa: F401  (the fixture)
from tests.test_wikipedia import SEARCH, SET_UP, SUMMARY, FakeHTTP


def run(topic: str, said: tuple[str, ...], owner_words: bool, mode: str = "text") -> Any:
    http = FakeHTTP((200, SEARCH), (200, SUMMARY))
    token = current_turn.set(new_turn(said, mode))
    try:
        return WikipediaLookup("en", SET_UP, http, owner_words=owner_words).lookup(topic)
    finally:
        current_turn.reset(token)


def test_f1_the_main_assistants_topic_is_in_the_owners_words() -> None:
    assert run("Pune", ("what is Pune known for?",), owner_words=True)["title"] == "India"
    assert run("Pune history", ("tell me about it", "Pune history please"), owner_words=True)
    for topic, said in [
        ("Pune Koregaon Park", ("what is Pune known for?",)),  # a word from a memory note
        ("Pune", ("I live in Pune", "nice", "what is it known for?")),  # an older message
    ]:
        with pytest.raises(ToolError) as caught:
            run(topic, said, owner_words=True)
        assert caught.value.asks == "not_owner_words", topic
    # a bot with the direct browser: the leak check only, as its own search
    assert run("Pune Koregaon Park", ("what is Pune known for?",), owner_words=False)


def test_f1_the_wiring(harness: Any) -> None:  # noqa: F811
    deps = harness.deps
    deps.connectors.update("browser", enabled=True, settings=None)
    main = deps.bots.get(MAIN_ID)
    assistant, _ = deps.make_assistant(main)
    assert assistant.tools.get(WIKI_TOOL).handler.__self__.owner_words is True
    direct, _ = deps.make_assistant(main.model_copy(update={"connectors": ["browser"]}))
    assert direct.tools.get(WIKI_TOOL).handler.__self__.owner_words is False


def test_w2_a_long_number_cannot_leave_in_pieces(
    conv: BrowserConversation,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half by search, half by Wikipedia, in one turn: checked together."""
    monkeypatch.setattr(tools_module, "BrowserTabs", FakeTabs)
    said = ("my account number is 98765 4321", "look these two up")
    token = current_turn.set(new_turn(said, "text"))
    try:
        conv.search("98765")
        lookup = WikipediaLookup("en", SET_UP, FakeHTTP())
        with pytest.raises(ToolError, match="long numbers") as caught:
            lookup.lookup("4321")
        assert caught.value.asks == "not_private"
        assert current_turn.get().queries == ["98765"]  # type: ignore[union-attr]
    finally:
        current_turn.reset(token)


@pytest.mark.parametrize("key", [".", ".."])
def test_dot_keys_are_refused(key: str) -> None:
    http = FakeHTTP((200, {"pages": [{"key": key, "title": key}]}))
    token = current_turn.set(new_turn(("Search for India on Wikipedia",), "text"))
    try:
        with pytest.raises(ToolError) as caught:
            WikipediaLookup("en", SET_UP, http).lookup("India")
    finally:
        current_turn.reset(token)
    assert caught.value.asks == "nothing_found" and len(http.calls) == 1  # no summary call


def test_each_refusal_says_its_own_reason(settings: Settings) -> None:
    lines = prompt_lines(settings)
    truth = BrowseTruth(lines, "on Wikipedia")
    with pytest.raises(ToolError) as caught:
        run("India", ("Search for India on Wikipedia",), owner_words=False, mode="wake")
    assert caught.value.asks == "wake_turn"
    for label, line in [
        ("wake_turn", lines.wiki_wake),
        ("not_owner_words", lines.wiki_own_words),
        ("not_private", lines.wiki_refused),
        ("not_set_up", lines.wiki_not_set_up),
    ]:
        calls = [{"name": WIKI_TOOL, "ok": False, "asks": label}]
        assert truth.finish("India is…", calls)[0] == line


def test_from_wikipedia_is_said_by_the_server() -> None:
    said: list[str] = []
    cap = LookupCap(said.append)
    cap.feed("India is a country in South Asia. ")
    cap.finish(["en.wikipedia.org"])
    assert "".join(said).endswith("From Wikipedia.")
    named: list[str] = []
    cap = LookupCap(named.append)
    cap.feed("According to Wikipedia, India is a country. ")
    cap.finish(["en.wikipedia.org"])
    assert "From" not in "".join(named)  # already named: not said twice
    web: list[str] = []
    cap = LookupCap(web.append)
    cap.feed("It is sunny. ")
    cap.finish(["www.example.org"])
    assert "".join(web).endswith("From example.org.")  # other sites as before


def test_unset_config_is_still_refused_first() -> None:
    http = FakeHTTP()
    token = current_turn.set(new_turn(("what is Pune known for?",), "text"))
    try:
        with pytest.raises(ToolError) as caught:
            WikipediaLookup("en", WikipediaConfig(), http, owner_words=True).lookup("Pune")
    finally:
        current_turn.reset(token)
    assert caught.value.asks == "not_set_up" and not http.calls
