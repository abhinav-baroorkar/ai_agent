"""Memory and the browse helper (review nits, M5): the helper's requests never hold the
memory section or a note's text; its prompt is `helper_prompt()` alone. Fakes only."""

from __future__ import annotations

from pathlib import Path

from sasha.browser.helper import helper_prompt
from sasha.browser.tools import BrowserConversation
from sasha.config import Settings
from sasha.memory.prompt import memory_section
from sasha.pipeline.llm import ToolCall
from sasha.prompting import prompt_lines
from tests.memory_fakes import make_store
from tests.server_fakes import FakeLLM, resp
from tests.test_browse_helper import ANSWER, _all_text, _browse, _main_with_browse, conv  # noqa: F401

NOTE_SENTINEL = "NOTE-SENTINEL-M5-insulin-at-9"


def test_the_helper_never_sees_the_memory_section(
    settings: Settings,
    tmp_path: Path,
    conv: BrowserConversation,  # noqa: F811
) -> None:
    store, _ = make_store(tmp_path)
    store.add(f"I take {NOTE_SENTINEL}", "chat", "main")
    helper_llm = FakeLLM("helper")
    main, main_llm, helper = _main_with_browse(settings, tmp_path, conv, helper_llm)
    lines = prompt_lines(settings)
    main.prompt_extra = lambda: memory_section(store, lines)  # as deps.make_assistant does
    helper_llm.responses = [
        resp(calls=[ToolCall("search", {"q": "weather in pune today"})]),
        resp(ANSWER),
    ]
    main_llm.responses = [_browse("weather in pune today"), resp("21 C, from x.")]
    main.handle_text("what is the weather in pune today?")

    helper_text = _all_text(helper_llm)
    assert NOTE_SENTINEL in _all_text(main_llm)  # the main model did see the note
    assert NOTE_SENTINEL not in helper_text and lines.memory_head not in helper_text
    assert helper_llm.seen[0][0][0]["content"] == helper_prompt(lines)
    assert helper.assistant().prompt_extra is None  # the helper has no memory section
    assert lines.memory_head not in helper_prompt(lines)
