"""Follow-ups from the take-over review (reviews/takeover-keeps-page-85451cc.md): K1, the
start page's script, and the person-check label on the tool frame and the reply."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sasha.browser import tools as tools_module
from sasha.browser.profile import prepare_profile
from sasha.browser.takeover_files import CHECK_PAGE, start_page
from sasha.browser.tools import BrowserConversation
from sasha.config import Settings
from sasha.conversation import current_turn, new_turn
from sasha.pipeline.llm import ToolCall
from sasha.tools import Tool, ToolError
from tests.server_fakes import WS, Harness, resp
from tests.test_browser_takeover import TEMPLATE, _rig, _until
from tests.test_browser_wiring import FakeService, FakeTabs, conv  # noqa: F401  (fixture)
from tests.test_server_ws import frames_until_idle, opened

BLOCKED = "https://html.duckduckgo.com/html/?q=India"


def test_k1_results_mean_no_check_waits(
    conv: BrowserConversation,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools_module, "BrowserTabs", FakeTabs)
    before = FakeService.forgotten
    token = current_turn.set(new_turn(("search for India",), "text"))
    try:
        conv.search("India")
    finally:
        current_turn.reset(token)
    assert FakeService.forgotten == before + 1


def test_k1_a_start_that_failed_keeps_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """He never saw his window, so the next take-over still opens the check."""

    async def main() -> None:
        service, t, spawned = _rig(tmp_path, monkeypatch)
        service.person_check_met(BLOCKED)
        working = t.spawn

        async def broken(*argv: str, **kw: Any) -> Any:
            raise OSError("no display after all")

        t.spawn = broken
        await t.start()
        await _until(lambda: t.mode == "assistant")
        assert service.blocked_search() == BLOCKED  # kept
        t.spawn = working
        await t.start()
        await _until(lambda: t.mode == "owner")
        assert spawned[-1][0][-1].endswith(CHECK_PAGE)  # it opens the check
        spawned[-1][2].closed.set()
        await _until(lambda: t.mode == "assistant")
        assert service.blocked_search() is None  # his window ran: now forgotten
        await service.stop()

    asyncio.run(main())


def test_the_start_page_escapes_angle_brackets(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    hostile = "https://html.duckduckgo.com/html/?q=</script><script>alert(1)</script>"
    start_page(root, TEMPLATE, hostile)
    html = (root / CHECK_PAGE).read_text()
    assert html.count("</script>") == 1 and "\\u003c/script>" in html


def test_the_label_is_on_the_tool_frame_and_the_reply(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For the page (design review L2): key on the server's fixed label, not on text."""
    h = Harness(settings, tmp_path)

    def checked(q: str) -> Any:
        err = ToolError("The search site wants to check you're a person")
        err.asks = "person_check"
        raise err

    make = h.deps.make_assistant

    def with_search(bot: Any, spoken: bool = False) -> Any:
        assistant, warnings = make(bot, spoken)
        obj = {"type": "object", "properties": {"q": {"type": "string"}}}
        assistant.tools.register(Tool("search", "Search the web.", obj, checked))
        return assistant, warnings

    monkeypatch.setattr(h.deps, "make_assistant", with_search)
    calls = [ToolCall("search", {"q": "India"}), ToolCall("fake_echo", {"text": "x"})]
    h.llm("big-model").responses = [resp("", calls), resp("Done")]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "search for India"})
        frames = frames_until_idle(ws, after_first=False)
    assert {"type": "tool", "name": "search", "ok": False, "asks": "person_check"} in frames
    assert {"type": "tool", "name": "fake_echo", "ok": True} in frames  # no label: no field
    reply = next(f for f in frames if f["type"] == "reply")
    assert reply["tool_calls"] == [
        {"name": "search", "ok": False, "asks": "person_check"},
        {"name": "fake_echo", "ok": True},
    ]
