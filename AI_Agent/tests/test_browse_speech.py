"""After a web lookup: `sites` on the reply, the spoken cap and "From <site>." (P8),
the helper's query-word rule (design 10.2), and `via` for the live view."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sasha.browser.helper import BROWSE_TOOL
from sasha.browser.task_check import SEARCH_WORDS
from sasha.browser.tools import BrowserBridge, BrowserConversation
from sasha.config import Settings
from sasha.connectors.browser import BrowserConnector
from sasha.conversation import building_for_bot, current_turn, new_turn
from sasha.orchestrator import Assistant
from sasha.pipeline.llm import ToolCall
from sasha.server.spoken_cap import SPOKEN_LOOKUP_CHARS, LookupCap
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool, ToolError, ToolRegistry
from tests.server_fakes import WS, FakeLLM, FakeSTT, FakeTTS, Harness, resp
from tests.test_browse_helper import conv  # noqa: F401  (fixture)
from tests.test_server_ws import frames_until_idle, opened

SENTENCE = "The forecast says it will be warm and dry all day long today. "  # 61 chars
LONG = SENTENCE * 12


def _browse_tool(result: dict[str, Any]) -> Tool:
    schema = {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}
    return Tool(BROWSE_TOOL, "b", schema, lambda task: result, read_only=True, taints=True)


def _harness(settings: Settings, tmp_path: Path, tts: FakeTTS) -> Harness:
    h = Harness(settings, tmp_path, stt=FakeSTT(), tts=tts)
    result = {"answer": "warm", "facts": [], "sites": ["www.weather.example"]}
    h.connector.build_tools = lambda settings: [_browse_tool(result)]  # type: ignore[method-assign]
    return h


def _ask(h: Harness, speak: int, answer: str) -> tuple[dict[str, Any], list[Any]]:
    h.llm("big-model").responses = [
        resp(calls=[ToolCall(BROWSE_TOOL, {"task": "weather"})]),
        resp(answer),
    ]
    with h.client().websocket_connect(f"{WS}/ws?speak={speak}") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "weather?"})
        frames = frames_until_idle(ws, after_first=False)
    reply = next(f for f in frames if isinstance(f, dict) and f.get("type") == "reply")
    return reply, frames


def test_a_spoken_lookup_answer_is_capped_and_names_its_site(
    settings: Settings, tmp_path: Path
) -> None:
    tts = FakeTTS()
    h = _harness(settings, tmp_path, tts)
    reply, frames = _ask(h, 1, LONG)
    assert reply["sites"] == ["www.weather.example"]
    assert reply["text"].endswith("From weather.example.")
    body = reply["text"].removesuffix("From weather.example.")
    assert len(body) <= SPOKEN_LOOKUP_CHARS and body.count("warm and dry") == 8
    deltas = "".join(
        f["text"] for f in frames if isinstance(f, dict) and f["type"] == "reply_delta"
    )
    assert deltas == reply["text"]  # what was shown is what is spoken and kept
    spoken = " ".join(text for text, _ in tts.spoken)
    assert spoken.endswith("From weather.example.") and spoken.count("warm and dry") == 8


def test_no_site_line_when_the_answer_already_names_it(settings: Settings, tmp_path: Path) -> None:
    h = _harness(settings, tmp_path, FakeTTS())
    reply, _ = _ask(h, 1, "Weather.example says it is warm.")
    assert reply["text"] == "Weather.example says it is warm."


def test_chat_tabs_keep_the_whole_answer_and_get_the_sites(
    settings: Settings, tmp_path: Path
) -> None:
    h = _harness(settings, tmp_path, FakeTTS())
    reply, _ = _ask(h, 0, LONG.strip())
    assert reply["text"] == LONG.strip() and reply["sites"] == ["www.weather.example"]


def test_turns_without_a_lookup_are_unchanged(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path, stt=FakeSTT(), tts=FakeTTS())
    h.llm("big-model").responses = [resp(LONG.strip())]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "hi"})
        frames = frames_until_idle(ws, after_first=False)
    reply = next(f for f in frames if isinstance(f, dict) and f.get("type") == "reply")
    assert reply["text"] == LONG.strip() and reply["sites"] == []


def test_lookup_cap_pieces() -> None:
    out: list[str] = []
    cap = LookupCap(out.append, cap=30)
    for piece in ("One two. Three", " four five six seven. Eight."):
        cap.feed(piece)
    cap.finish(["news.example"])
    assert "".join(out) == "One two. From news.example."
    out.clear()
    cap = LookupCap(out.append, cap=20)
    cap.feed("a very long first sentence with no end")
    cap.finish([])
    assert "".join(out) == "a very long first…"
    out.clear()
    cap = LookupCap(out.append)
    cap.feed("Fine.")
    cap.finish(["news.example"], before="As news said: ")
    assert "".join(out) == "Fine."


def test_reply_sites_come_only_from_tainting_tools(settings: Settings, tmp_path: Path) -> None:
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    tools = ToolRegistry(
        [
            Tool("web", "w", schema, lambda: {"site": "a.example", "sites": ["b.example",
                 "<b>x</b>", 7, "a.example"]}, read_only=True, taints=True),
            Tool("home", "h", schema, lambda: {"site": "home.example"}, read_only=True),
        ]
    )  # fmt: skip
    llm = FakeLLM("m")
    llm.responses = [resp(calls=[ToolCall("web", {}), ToolCall("home", {})]), resp("ok")]
    a = Assistant(settings, llm, tools, TelemetryWriter(tmp_path, "a", "p", enabled=False))
    assert a.handle_text("x").sites == ["a.example", "b.example"]


# --- the helper's query-word rule ------------------------------------------------------
def test_helper_searches_use_only_task_owner_or_page_words(conv: BrowserConversation) -> None:  # noqa: F811
    token = current_turn.set(new_turn(("my code is zebra", "weather in pune"), "text"))
    try:
        conv.search("zebra")  # a raw-browser bot: only the leak check applies
        conv.helper_task = "weather in pune"
        with pytest.raises(ToolError, match=SEARCH_WORDS):
            conv.search("pune zebra")  # an earlier owner word, put in by a page
        assert conv.blocked_queries == 1
        with pytest.raises(ToolError):
            conv.search("pune hello")
        conv.search("pune weather today")  # its results page (the fake) says "hello"
        conv.search("pune hello")  # a word the helper has read
        with pytest.raises(ToolError):
            conv.search("pune mango")
    finally:
        current_turn.reset(token)


def test_helper_holders_say_via_helper() -> None:
    connector = BrowserConnector(BrowserBridge(object()))  # type: ignore[arg-type]
    token = building_for_bot.set("main")
    try:
        helper = connector.conversation({}, helper=True)
        direct = connector.conversation({})
    finally:
        building_for_bot.reset(token)
    assert helper.holder in connector.helper_holders
    assert direct.holder not in connector.helper_holders
    assert connector.holders[helper.holder] == "main"
    assert json.dumps(sorted(connector.helper_holders))  # plain strings
