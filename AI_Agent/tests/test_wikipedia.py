"""The Wikipedia lookup (browser/wikipedia.py; owner's decision 2026-11-16): server-built
addresses on the wiki's own host, the browser's rules (leak check, taint, the server's
line when it fails), the site named. A faked HTTP layer; one real call, skipped unless
WIKIPEDIA_LIVE=1 (it reaches en.wikipedia.org with the configured User-Agent)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from sasha.bots import MAIN_ID, VOICE_ID
from sasha.browser.wikipedia import (
    FAILED,
    NOTHING,
    WIKI_TOOL,
    WikipediaLookup,
    http_get,
    wikipedia_tool,
)
from sasha.config import Settings
from sasha.config_wikipedia import REPO_URL, WikipediaConfig
from sasha.conversation import current_turn, new_turn
from sasha.orchestrator import Assistant
from sasha.pipeline.llm import ToolCall
from sasha.prompting import prompt_lines
from sasha.server.browse_truth import BrowseTruth
from sasha.telemetry import TelemetryWriter
from sasha.tools import ToolError, ToolRegistry
from tests.server_fakes import FakeLLM, resp
from tests.test_browse_taint import harness  # noqa: F401  (the fixture)

SEARCH = {
    "pages": [
        {
            "id": 14533,
            "key": "India",
            "title": "India",
            "excerpt": '<span class="searchmatch">India</span>, officially the Republic',
            "description": "Country in South Asia",
        }
    ]
}
SUMMARY = {"title": "India", "extract": "India, officially the Republic of India, is a country."}


class FakeHTTP:
    def __init__(self, *answers: tuple[int, Any] | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, Any]:
        self.calls.append((url, headers, timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def look(http: FakeHTTP, topic: str = "India", said: str = "Search for India on Wikipedia",
         mode: str = "text") -> dict[str, Any]:  # fmt: skip
    token = current_turn.set(new_turn((said,), mode))
    try:
        return WikipediaLookup("en", WikipediaConfig(), http).lookup(topic)
    finally:
        current_turn.reset(token)


def test_two_server_built_requests_and_an_untrusted_result() -> None:
    http = FakeHTTP((200, SEARCH), (200, SUMMARY))
    out = look(http, topic="India  history")
    search, summary = http.calls
    parts = urlsplit(search[0])
    assert (parts.scheme, parts.hostname, parts.path) == (
        "https",
        "en.wikipedia.org",
        "/w/rest.php/v1/search/page",
    )
    assert parse_qs(parts.query) == {"q": ["India history"], "limit": ["1"]}
    assert summary[0] == "https://en.wikipedia.org/api/rest_v1/page/summary/India"
    ua = search[1]["User-Agent"]
    assert "bot" in ua and REPO_URL in ua and "@" not in ua  # policy: contact, no email
    assert search[2] == WikipediaConfig().timeout_s
    assert out["site"] == "en.wikipedia.org" and out["title"] == "India"
    assert (
        out["url"] == "https://en.wikipedia.org/wiki/India" and "never instructions" in out["note"]
    )
    assert out["extract"].startswith("India, officially")


def test_a_failed_summary_falls_back_to_the_search_text() -> None:
    out = look(FakeHTTP((200, SEARCH), (404, None)))
    assert out["extract"] == "Country in South Asia India, officially the Republic"
    assert "<span" not in out["extract"]


def test_the_page_key_is_escaped_into_our_own_host() -> None:
    odd = {"pages": [{"key": "../../evil.example/x?y=1#z", "title": "x"}]}
    http = FakeHTTP((200, odd), (404, None))
    out = look(http)
    summary_url = http.calls[1][0]
    assert urlsplit(summary_url).hostname == "en.wikipedia.org"
    assert "/../" not in summary_url and "?" not in summary_url and "#" not in summary_url
    assert urlsplit(out["url"]).hostname == "en.wikipedia.org"


@pytest.mark.parametrize(
    ("answers", "message", "asks"),
    [
        (((200, {"pages": []}),), NOTHING, "nothing_found"),
        (((503, None),), FAILED, "failed"),
        ((TimeoutError("slow"),), FAILED, "failed"),
    ],
)
def test_failures_are_plain_and_labelled(answers: Any, message: str, asks: str) -> None:
    with pytest.raises(ToolError) as caught:
        look(FakeHTTP(*answers))
    assert str(caught.value) == message and caught.value.asks == asks
    assert "slow" not in str(caught.value)  # no details from the network to the model


def test_the_browsers_rules_apply() -> None:
    with pytest.raises(ToolError, match="email"):  # the same leak check as search
        look(FakeHTTP(), topic="someone@example.org")
    with pytest.raises(ToolError, match="wake-word"):
        look(FakeHTTP(), mode="wake")
    with pytest.raises(ToolError, match="conversation"):
        WikipediaLookup("en", WikipediaConfig(), FakeHTTP()).lookup("India")
    with pytest.raises(ValueError):
        WikipediaLookup("en.evil.example/", WikipediaConfig())
    tool = wikipedia_tool(WikipediaLookup("en", WikipediaConfig(), FakeHTTP()))
    assert tool.read_only and tool.taints  # outside text: the change guard applies after it


def test_the_site_is_named_and_the_result_is_wrapped(settings: Settings, tmp_path: Path) -> None:
    lookup = WikipediaLookup("en", WikipediaConfig(), FakeHTTP((200, SEARCH), (200, SUMMARY)))
    llm = FakeLLM("main")
    llm.responses = [resp(calls=[ToolCall(WIKI_TOOL, {"topic": "India"})]), resp("A country.")]
    telemetry = TelemetryWriter(tmp_path, "a", "p", enabled=False)
    a = Assistant(settings, llm, ToolRegistry([wikipedia_tool(lookup)]), telemetry)
    reply = a.handle_text("What is India? Look it up on Wikipedia.")
    assert reply.sites == ["en.wikipedia.org"] and reply.tool_calls[0]["ok"]
    tool_message = next(m for m in llm.seen[1][0] if m["role"] == "tool")
    assert "India, officially" in tool_message["content"]
    wrapped = json.loads(tool_message["content"])  # labelled as data, never instructions
    assert wrapped["source"] == WIKI_TOOL and wrapped["untrusted_data"]["title"] == "India"


def test_the_server_says_a_failed_lookup(settings: Settings) -> None:
    lines = prompt_lines(settings)
    truth = BrowseTruth(lines, "Search for India on Wikipedia")
    model = "India is a country of 1.4 billion people."  # its own memory, as if looked up
    failed = [{"name": WIKI_TOOL, "ok": False, "asks": "nothing_found"}]
    assert truth.finish(model, failed)[0] == lines.wiki_nothing_found
    down = [{"name": WIKI_TOOL, "ok": False, "asks": "failed"}]
    assert truth.finish(model, down)[0] == lines.wiki_failed
    mixed = [{"name": "browse", "ok": False, "asks": "nothing_found"}, *failed]
    assert truth.finish(model, mixed)[0] == lines.web_nothing_found  # not only Wikipedia
    worked = [*failed, {"name": "browse", "ok": True}]
    assert truth.finish(model, worked)[0] == model


def test_who_gets_it(harness: Any) -> None:  # noqa: F811
    deps = harness.deps
    main = deps.bots.get(MAIN_ID)
    assistant, _ = deps.make_assistant(main)
    assert WIKI_TOOL not in assistant.tools  # the browser connector is off by default
    deps.connectors.update("browser", enabled=True, settings=None)
    assistant, _ = deps.make_assistant(main)
    assert WIKI_TOOL in assistant.tools and assistant.tools.get(WIKI_TOOL).taints
    assert "Wikipedia" in assistant.system_prompt  # the abilities name it
    direct, _ = deps.make_assistant(main.model_copy(update={"connectors": ["browser"]}))
    assert WIKI_TOOL in direct.tools and "search" in direct.tools
    voice, _ = deps.make_assistant(deps.bots.get(VOICE_ID))
    assert WIKI_TOOL not in voice.tools  # wake turns: no lookups
    assert deps.wikipedia().host == f"{deps.settings.assistant.languages.default}.wikipedia.org"
    rows = harness.client().get("/api/connectors").json()["connectors"]
    assert WIKI_TOOL in next(r["tools"] for r in rows if r["id"] == "browser")


@pytest.mark.skipif(os.environ.get("WIKIPEDIA_LIVE") != "1", reason="reaches Wikipedia")
def test_one_real_lookup() -> None:
    """Run by hand: WIKIPEDIA_LIVE=1 uv run pytest tests/test_wikipedia.py -k real"""
    token = current_turn.set(new_turn(("Look up Python on Wikipedia",), "text"))
    try:
        out = WikipediaLookup("en", WikipediaConfig(), http_get).lookup(
            "Python programming language"
        )
    finally:
        current_turn.reset(token)
    assert out["url"].startswith("https://en.wikipedia.org/wiki/")
    assert "Python" in out["title"] and len(out["extract"]) > 50
