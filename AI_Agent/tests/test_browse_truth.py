"""A web lookup that brought nothing back is said as it is (found live: a blocked search
got the model's own old answer as if from the web)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sasha.browser.chrome import chrome_flags
from sasha.browser.helper import BROWSE_TOOL, BrowseHelper, LookupFailed
from sasha.browser.pages import PERSON_CHECK
from sasha.browser.profile import prepare_profile
from sasha.browser.takeover_files import clear_extensions
from sasha.browser.tools import BrowserConversation
from sasha.config import Settings
from sasha.conversation import current_turn, new_turn
from sasha.pipeline.llm import ToolCall
from sasha.prompting import prompt_lines
from sasha.server.browse_truth import BrowseTruth
from sasha.server.turn_check import ChainCheck
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool
from tests.server_fakes import WS, FakeSTT, FakeTTS, Harness, resp
from tests.test_browse_helper import conv  # noqa: F401  (fixture)
from tests.test_server_ws import frames_until_idle, opened

OLD_KNOWLEDGE = "The latest stable Python version is 3.12.x (as of early 2025)."


def _failed(reason: str) -> list[dict[str, Any]]:
    return [{"name": BROWSE_TOOL, "arguments": {}, "ok": False, "asks": reason}]


def test_the_server_line_replaces_an_unmarked_answer(settings: Settings) -> None:
    lines = prompt_lines(settings)
    truth = BrowseTruth(lines)
    assert truth.finish(OLD_KNOWLEDGE, _failed("person_check"))[1] == PERSON_CHECK
    assert truth.finish(OLD_KNOWLEDGE, _failed("nothing_found"))[1] == lines.web_nothing_found
    assert truth.finish(OLD_KNOWLEDGE, _failed(""))[1] == lines.web_failed
    marked = f"Sorry. {lines.web_unchecked} Python 3.12 was current."
    assert truth.finish(marked, _failed("person_check"))[1] == (
        f"{PERSON_CHECK} {lines.web_unchecked} Python 3.12 was current."
    )
    worked = [{"name": BROWSE_TOOL, "ok": True}, *_failed("nothing_found")]
    assert truth.finish("From python.org: 3.14.", worked)[1] == "From python.org: 3.14."
    assert truth.finish("Hi.", [])[1] == "Hi."


def test_a_failed_lookup_holds_the_rest_and_never_resends(settings: Settings) -> None:
    truth = BrowseTruth(prompt_lines(settings))
    chain = ChainCheck([truth])
    assert chain.feed("Let me check. ") == "Let me check. "  # before the lookup: sent
    chain.on_tool(BROWSE_TOOL, False)
    assert chain.holding and chain.feed(OLD_KNOWLEDGE) == ""  # held
    rest, final = chain.finish(f"Let me check. {OLD_KNOWLEDGE}", _failed("person_check"))
    assert final == PERSON_CHECK and rest == PERSON_CHECK


def test_a_lookup_with_no_page_is_a_failure(
    settings: Settings,
    tmp_path: Path,
    conv: BrowserConversation,  # noqa: F811
) -> None:
    from tests.server_fakes import FakeLLM

    llm = FakeLLM("helper")
    llm.responses = [resp(json.dumps({"answer": OLD_KNOWLEDGE}))]  # from memory, no tool
    helper = BrowseHelper(conv, settings, llm, TelemetryWriter(tmp_path, "a", "p", enabled=False),
                          prompt_lines(settings))  # fmt: skip
    token = current_turn.set(new_turn(("latest stable python version",), "text"))
    try:
        helper.browse("latest stable python version")
    except LookupFailed as e:
        assert e.asks == "nothing_found"
        assert prompt_lines(settings).web_unchecked in str(e)  # the marker it may use
    else:
        raise AssertionError("an answer with no page must not pass as a web result")
    finally:
        current_turn.reset(token)


def _harness(settings: Settings, tmp_path: Path, tts: FakeTTS | None = None) -> Harness:
    h = Harness(settings, tmp_path, stt=FakeSTT(), tts=tts or FakeTTS())

    def browse(task: str) -> Any:
        raise LookupFailed("person_check", prompt_lines(settings).web_unchecked)

    schema = {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}
    tool = Tool(BROWSE_TOOL, "b", schema, browse, read_only=True, taints=True)
    h.connector.build_tools = lambda settings: [tool]  # type: ignore[method-assign]
    h.llm("big-model").responses = [
        resp(calls=[ToolCall(BROWSE_TOOL, {"task": "latest stable python version"})]),
        resp(OLD_KNOWLEDGE),
    ]
    return h


def _turn(h: Harness, speak: int) -> list[Any]:
    with h.client().websocket_connect(f"{WS}/ws?speak={speak}") as ws:
        opened(ws)
        ws.send_json(
            {"type": "text", "text": "Search the web for the latest stable Python version"}
        )
        return frames_until_idle(ws, after_first=False)


def test_a_typed_turn_shows_only_the_truth(settings: Settings, tmp_path: Path) -> None:
    frames = _turn(_harness(settings, tmp_path), 0)
    reply = next(f for f in frames if isinstance(f, dict) and f.get("type") == "reply")
    shown = "".join(f["text"] for f in frames if isinstance(f, dict) and f["type"] == "reply_delta")
    assert reply["text"] == PERSON_CHECK and "3.12" not in shown


def test_a_spoken_turn_never_says_the_old_answer(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    frames = _turn(_harness(settings, tmp_path, tts), 1)
    reply = next(f for f in frames if isinstance(f, dict) and f.get("type") == "reply")
    spoken = " ".join(text for text, _ in tts.spoken)
    assert "3.12" not in spoken and "check you're a person" in spoken
    assert "3.12" not in reply["text"]


def test_no_extensions_in_the_headless_chrome(tmp_path: Path) -> None:
    assert "--disable-extensions" in chrome_flags(tmp_path, 9)  # review S1
    root = prepare_profile(tmp_path)
    folders = ("Extension Scripts", "Sync Extension Settings", "Managed Extension Settings")
    for rel in folders:
        (root / "Default" / rel).mkdir(parents=True)
    clear_extensions(root)  # review S2
    assert not any((root / "Default" / rel).exists() for rel in folders)
