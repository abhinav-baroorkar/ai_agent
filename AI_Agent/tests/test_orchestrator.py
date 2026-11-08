from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from sasha.config import Settings
from sasha.orchestrator import Assistant
from sasha.pipeline.llm import LLMResponse, Message, ToolCall
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool, ToolRegistry


class ScriptedLLM:
    """Returns queued responses; records what it was sent."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[list[Message], bool]] = []

    @property
    def model_id(self) -> str:
        return "fake"

    def model_version(self) -> str:
        return "fake@1"

    def chat(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None = None
    ) -> LLMResponse:
        self.calls.append((list(messages), tools is not None))
        return self.responses.pop(0)


def _resp(content: str = "", calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(content, calls or [], "fake", {"role": "assistant", "content": content})


def _assistant(settings: Settings, llm: ScriptedLLM, tmp_path: Path) -> Assistant:
    tool = Tool(
        "get_state",
        "x",
        {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
        },
        lambda entity_id: {"entity_id": entity_id, "state": "on"},
    )
    writer = TelemetryWriter(tmp_path, settings.assistant.name, settings.profile.name)
    return Assistant(settings, llm, ToolRegistry([tool]), writer)


def test_plain_answer(settings: Settings, tmp_path: Path) -> None:
    llm = ScriptedLLM([_resp("Hi there")])
    reply = _assistant(settings, llm, tmp_path).handle_text("hello")
    assert reply.text == "Hi there"
    system = llm.calls[0][0][0]
    assert system["role"] == "system"
    assert settings.assistant.name in system["content"]


def test_tool_call_round_trip_and_telemetry(settings: Settings, tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [_resp(calls=[ToolCall("get_state", {"entity_id": "light.a"})]), _resp("It is on.")]
    )
    a = _assistant(settings, llm, tmp_path)
    reply = a.handle_text("is the light on?")
    assert reply.text == "It is on."
    assert reply.tool_calls == [
        {"name": "get_state", "arguments": {"entity_id": "light.a"}, "ok": True}
    ]
    tool_msg = llm.calls[1][0][-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["state"] == "on"

    names = [s["name"] for s in reply.telemetry["stages"]]
    assert names == ["llm", "tool:get_state", "llm"]
    assert reply.telemetry["models"] == {"llm": "fake@1"}
    log = json.loads(a.telemetry.path_for_today().read_text().splitlines()[0])
    assert log["success"] is True


def test_last_round_withholds_tools(settings: Settings, tmp_path: Path) -> None:
    rounds = settings.profile.llm.max_tool_rounds
    looping = [_resp(calls=[ToolCall("get_state", {"entity_id": "light.a"})])] * rounds
    llm = ScriptedLLM([*looping, _resp("Done")])
    assert _assistant(settings, llm, tmp_path).handle_text("x").text == "Done"
    assert [offered for _, offered in llm.calls] == [True] * rounds + [False]


def test_history_kept_and_reset(settings: Settings, tmp_path: Path) -> None:
    llm = ScriptedLLM([_resp("one"), _resp("two"), _resp("three")])
    a = _assistant(settings, llm, tmp_path)
    a.handle_text("first")
    a.handle_text("second")
    assert [m["content"] for m in llm.calls[1][0][1:]] == ["first", "one", "second"]
    a.reset()
    a.handle_text("third")
    assert len(llm.calls[2][0]) == 2


def test_llm_failure_logged(settings: Settings, tmp_path: Path) -> None:
    class Boom(ScriptedLLM):
        def chat(self, messages: Sequence[Message], tools: Any = None) -> LLMResponse:
            raise RuntimeError("down")

    a = _assistant(settings, Boom([]), tmp_path)
    with pytest.raises(RuntimeError):
        a.handle_text("x")
    log = json.loads(a.telemetry.path_for_today().read_text())
    assert log["success"] is False and "down" in log["error"]
