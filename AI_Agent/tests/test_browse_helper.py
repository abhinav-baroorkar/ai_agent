"""The browse helper (browser-design.md §10, P4-P7, §10.0a): what goes into its LLM
requests, what comes out, and its limits. No Chrome: a fake page layer."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sasha import prompting
from sasha.browser import helper as helper_module
from sasha.browser import tools as tools_module
from sasha.browser.helper import (
    BROWSE_TOOL,
    TOO_LONG,
    BrowseHelper,
    browse_tool,
    helper_prompt,
    shape_result,
)
from sasha.browser.tools import BrowserBridge, BrowserConversation
from sasha.config import Settings
from sasha.connectors.browser import TOOL_NAMES
from sasha.conversation import current_turn, new_turn, on_turn_end
from sasha.orchestrator import Assistant
from sasha.pipeline.llm import ToolCall
from sasha.prompting import prompt_lines
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool, ToolError, ToolRegistry
from tests.server_fakes import FakeLLM, resp
from tests.test_browser_wiring import FakeService, FakeTabs

OWNER_SENTINEL = "zebra-7731-owner-sentinel"
SYSTEM_SENTINEL = "SYSTEM-SENTINEL-QX9"
ANSWER = json.dumps({"answer": "It is 21 C.", "facts": [{"text": "21 C", "site": "x"}]})


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
    yield BrowserConversation(bridge, "helper-1", "https://search.example/?q={q}")
    loop.call_soon_threadsafe(loop.stop)
    thread.join(2)
    loop.close()


def _telemetry(tmp_path: Path) -> TelemetryWriter:
    return TelemetryWriter(tmp_path, "a", "p", enabled=False)


def _main_with_browse(
    settings: Settings, tmp_path: Path, conv: BrowserConversation, helper_llm: FakeLLM
) -> tuple[Assistant, FakeLLM, BrowseHelper]:
    helper = BrowseHelper(conv, settings, helper_llm, _telemetry(tmp_path), prompt_lines(settings))
    main_llm = FakeLLM("main")
    main = Assistant(
        settings,
        main_llm,
        ToolRegistry([browse_tool(helper)]),
        _telemetry(tmp_path),
        system_prompt=f"You are the main assistant. {SYSTEM_SENTINEL}",
    )
    return main, main_llm, helper


def _browse(task: str) -> Any:
    return resp(calls=[ToolCall(BROWSE_TOOL, {"task": task})])


def _all_text(llm: FakeLLM) -> str:
    return json.dumps([messages for messages, _ in llm.seen])


# --- §10.0a: the sentinel tests (first commit) -----------------------------------------
def test_helper_requests_never_hold_earlier_owner_words_or_the_main_prompt(
    settings: Settings, tmp_path: Path, conv: BrowserConversation
) -> None:
    helper_llm = FakeLLM("helper")
    main, main_llm, _ = _main_with_browse(settings, tmp_path, conv, helper_llm)
    main_llm.responses = [resp("Noted.")]
    main.handle_text(f"my locker code is {OWNER_SENTINEL}")
    helper_llm.responses = [
        resp(calls=[ToolCall("search", {"q": "weather in pune today"})]),
        resp(ANSWER),
    ]
    main_llm.responses = [_browse("weather in pune today"), resp("21 C, from x.")]
    reply = main.handle_text("what is the weather in pune today?")

    assert reply.text == "21 C, from x."
    assert len(helper_llm.seen) == 2  # the helper really ran, with a tool round
    helper_text = _all_text(helper_llm)
    assert OWNER_SENTINEL not in helper_text  # P5: never the main history
    assert SYSTEM_SENTINEL not in helper_text  # never the main system prompt
    assert OWNER_SENTINEL in _all_text(main_llm)  # (the main model did see it)
    first = helper_llm.seen[0][0]
    assert [m["role"] for m in first] == ["system", "user"]  # its prompt and the task only
    assert first[0]["content"] == helper_prompt(prompt_lines(settings))
    assert first[1]["content"] == "weather in pune today"
    later = helper_llm.seen[1][0]
    assert [m["role"] for m in later[2:]] == ["assistant", "tool"]  # then its own results


def test_the_helper_prompt_is_not_built_by_build_prompt(
    settings: Settings, tmp_path: Path, conv: BrowserConversation, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("the helper must not use build_prompt")

    monkeypatch.setattr(prompting, "build_prompt", refuse)
    assert not hasattr(helper_module, "build_prompt")
    lines = prompt_lines(settings)
    text = helper_prompt(lines)
    assert lines.can_head not in text and lines.never_claim not in text
    assert settings.assistant.name not in text  # no persona either
    helper_llm = FakeLLM("helper")
    main, main_llm, _ = _main_with_browse(settings, tmp_path, conv, helper_llm)
    helper_llm.responses = [resp(ANSWER)]
    main_llm.responses = [_browse("weather in pune"), resp("ok")]
    main.handle_text("weather in pune")
    assert helper_llm.seen[0][0][0]["content"] == text


def test_the_helper_has_the_browser_tools_and_nothing_else(
    settings: Settings, tmp_path: Path, conv: BrowserConversation
) -> None:
    helper = BrowseHelper(
        conv, settings, FakeLLM("h"), _telemetry(tmp_path), prompt_lines(settings)
    )
    names = helper.assistant().tools.names
    assert sorted(names) == sorted(TOOL_NAMES)
    for forbidden in ("remember", "list_memories", "forget", "call_service", "browse"):
        assert forbidden not in names
    helper_llm = FakeLLM("helper")
    main, main_llm, _ = _main_with_browse(settings, tmp_path, conv, helper_llm)
    helper_llm.responses = [resp(ANSWER)]
    main_llm.responses = [_browse("weather in pune"), resp("ok")]
    main.handle_text("weather in pune")
    assert all(set(offered) <= set(TOOL_NAMES) for offered in helper_llm.offered)


# --- inheriting the turn (design 10.2) -------------------------------------------------
def test_the_helper_runs_inside_the_main_turn(settings: Settings, tmp_path: Path) -> None:
    seen: list[Any] = []
    ended: list[str] = []

    def probe() -> dict[str, Any]:
        turn = current_turn.get()
        seen.append(turn)
        assert turn is not None
        on_turn_end(turn.turn_id, lambda: ended.append("end"))
        return {"ok": True}

    inner_llm = FakeLLM("inner")
    inner_llm.responses = [resp(calls=[ToolCall("probe", {})]), resp("done")]
    inner = Assistant(
        settings,
        inner_llm,
        ToolRegistry([Tool("probe", "p", {"type": "object", "properties": {}}, probe)]),
        _telemetry(tmp_path),
        history_turns=0,
    )

    def outer_tool() -> dict[str, Any]:
        seen.append(current_turn.get())
        inner.handle_text("model-written task", inherit_turn=True)
        assert ended == []  # an inherited turn doesn't end the owner's turn
        return {"ok": True}

    outer_llm = FakeLLM("outer")
    outer_llm.responses = [resp(calls=[ToolCall("outer", {})]), resp("fine")]
    outer = Assistant(
        settings,
        outer_llm,
        ToolRegistry([Tool("outer", "o", {"type": "object", "properties": {}}, outer_tool)]),
        _telemetry(tmp_path),
    )
    outer.handle_text("the owner's words")
    assert seen[0] is seen[1]  # the same TurnContext, so B1 checks the owner's words
    assert seen[1].user_messages == ("the owner's words",)
    assert ended == ["end"]  # ended once, with the main turn
    with pytest.raises(RuntimeError):
        inner.handle_text("no turn", inherit_turn=True)


# --- P4: provenance from the server ----------------------------------------------------
def test_sites_come_from_the_tabs_not_the_model(
    settings: Settings, tmp_path: Path, conv: BrowserConversation
) -> None:
    helper_llm = FakeLLM("helper")
    _, _, helper = _main_with_browse(settings, tmp_path, conv, helper_llm)
    claimed = {
        "answer": "Done",
        "facts": [{"text": "a", "site": "evil.example"}, {"text": "b", "site": "x"}],
        "sites": ["trusted.example"],
    }
    helper_llm.responses = [
        resp(calls=[ToolCall("search", {"q": "pune"})]),
        resp(json.dumps(claimed)),
    ]
    token = current_turn.set(new_turn(("pune weather",), "text"))
    try:
        out = helper.browse("pune weather")
    finally:
        current_turn.reset(token)
    assert out["sites"] == ["x"]  # the fake page layer's real site
    assert [f["site"] for f in out["facts"]] == ["", "x"]


def test_the_result_is_capped_and_cleaned() -> None:
    long = {
        "answer": "a‮b\x00" + "y" * 900,
        "facts": [{"text": "f" * 400, "site": "x"}] * 5,
    }
    out = shape_result(json.dumps(long), ["x", "w"])
    assert len(out["answer"]) == 500 and "‮" not in out["answer"] and "\x00" not in out["answer"]
    assert len(out["facts"]) == 3 and all(len(f["text"]) == 200 for f in out["facts"])
    plain = shape_result("not json at all " * 100, [])
    assert len(plain["answer"]) <= 500 and plain["facts"] == [] and plain["sites"] == []
    fenced = shape_result('```json\n{"answer": "hi"}\n```', [])
    assert fenced["answer"] == "hi"


# --- P6 / P7 ---------------------------------------------------------------------------
def test_one_holder_per_conversation_across_lookups(
    settings: Settings, tmp_path: Path, conv: BrowserConversation
) -> None:
    helper_llm = FakeLLM("helper")
    main, main_llm, helper = _main_with_browse(settings, tmp_path, conv, helper_llm)
    helper_llm.responses = [resp(ANSWER), resp(ANSWER)]
    main_llm.responses = [_browse("weather in pune"), resp("one")]
    main.handle_text("weather in pune")
    first = helper.conv
    main_llm.responses = [_browse("weather in mumbai"), resp("two")]
    main.handle_text("and weather in mumbai")
    assert helper.conv is first and helper.conv.holder == "helper-1"


def test_the_timeout_cancels_the_helpers_llm_call(
    settings: Settings, tmp_path: Path, conv: BrowserConversation
) -> None:
    helper_llm = FakeLLM("helper")
    helper_llm.hold = True  # waits until cancelled
    helper = BrowseHelper(
        conv, settings, helper_llm, _telemetry(tmp_path), prompt_lines(settings), timeout=0.05
    )
    token = current_turn.set(new_turn(("weather in pune",), "text"))
    try:
        with pytest.raises(ToolError, match=TOO_LONG):
            helper.browse("weather in pune")
    finally:
        current_turn.reset(token)
    assert helper_llm.started.is_set()


def test_the_owners_stop_reaches_the_helper(
    settings: Settings, tmp_path: Path, conv: BrowserConversation
) -> None:
    helper_llm = FakeLLM("helper")
    helper_llm.hold = True
    helper = BrowseHelper(conv, settings, helper_llm, _telemetry(tmp_path), prompt_lines(settings))
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()
    turn = new_turn(("weather in pune",), "text", should_stop=stop.is_set)
    token = current_turn.set(turn)
    try:
        with pytest.raises(ToolError, match="owner stopped"):
            helper.browse("weather in pune")
    finally:
        current_turn.reset(token)


def test_no_lookups_on_wake_turns_or_outside_a_turn(
    settings: Settings, tmp_path: Path, conv: BrowserConversation
) -> None:
    helper = BrowseHelper(
        conv, settings, FakeLLM("h"), _telemetry(tmp_path), prompt_lines(settings)
    )
    with pytest.raises(ToolError):
        helper.browse("weather")
    token = current_turn.set(new_turn(("weather",), "wake"))
    try:
        with pytest.raises(ToolError, match="wake"):
            helper.browse("weather")
    finally:
        current_turn.reset(token)


def test_main_gets_browse_without_the_internet_limit(settings: Settings) -> None:
    lines = prompt_lines(settings)
    with_browse = prompting.build_prompt("base", ["browse"], lines)
    without = prompting.build_prompt("base", [], lines)
    assert "go on the internet" in without and "go on the internet" not in with_browse
    assert lines.abilities["browse"] in with_browse
