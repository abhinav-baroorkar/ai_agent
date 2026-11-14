"""Runs one request through the stages. Phase 0: text in -> LLM + tools -> text out."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sasha.config import Settings
from sasha.pipeline.llm import LLM, Message
from sasha.telemetry import RequestTrace, TelemetryWriter
from sasha.tools import ToolRegistry


@dataclass
class Reply:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)


class RequestFailed(Exception):
    """A request failed. `tool_calls` lists tools that already ran (they may have acted).

    The original exception is the `__cause__`. Ctrl-C is never wrapped: KeyboardInterrupt
    propagates unchanged; `Assistant.last_tool_calls` then says what already ran.
    """

    def __init__(self, tool_calls: list[dict[str, Any]]) -> None:
        super().__init__("request failed")
        self.tool_calls = tool_calls


def wrap_untrusted(source: str, content: str) -> str:
    """Label tool output as data so the model never treats it as instructions."""
    try:
        data: Any = json.loads(content)
    except ValueError:
        data = content
    return json.dumps({"source": source, "untrusted_data": data}, ensure_ascii=False)


class Assistant:
    def __init__(
        self,
        settings: Settings,
        llm: LLM,
        tools: ToolRegistry,
        telemetry: TelemetryWriter,
        history_turns: int = 8,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.tools = tools
        self.telemetry = telemetry
        self.history_turns = history_turns
        self.system_prompt = settings.assistant.render_system_prompt()
        self._history: list[Message] = []
        self.last_tool_calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self.settings.assistant.name

    def reset(self) -> None:
        self._history.clear()

    def _messages(self, text: str) -> list[Message]:
        recent = self._history[-2 * self.history_turns :]
        return [
            {"role": "system", "content": self.system_prompt},
            *recent,
            {"role": "user", "content": text},
        ]

    def handle_text(self, text: str) -> Reply:
        trace = self.telemetry.start(input_mode="text")
        trace.models["llm"] = self.llm.model_version()
        trace.record_text("user", text)
        calls_made: list[dict[str, Any]] = []
        self.last_tool_calls = calls_made
        try:
            reply = self._run(text, trace, calls_made)
        except BaseException as exc:  # includes Ctrl-C: still write the telemetry line
            trace.finish(success=False, error=f"{type(exc).__name__}: {exc}")
            if isinstance(exc, Exception):
                raise RequestFailed(calls_made) from exc
            raise  # Ctrl-C, SystemExit: unchanged, so `except Exception` can't swallow them
        trace.record_text("assistant", reply.text)
        reply.telemetry = trace.finish(success=True)
        self._history += [
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply.text},
        ]
        return reply

    def _run(self, text: str, trace: RequestTrace, calls_made: list[dict[str, Any]]) -> Reply:
        messages = self._messages(text)
        max_rounds = self.settings.profile.llm.max_tool_rounds
        specs = self.tools.specs() if len(self.tools) else None

        for round_no in range(max_rounds + 1):
            # On the last round, withhold tools so the model must answer in words.
            offer_tools = specs if round_no < max_rounds else None
            with trace.stage("llm", round=round_no, model=self.llm.model_id) as meta:
                resp = self.llm.chat(messages, tools=offer_tools)
                meta.update(resp.stats)
                meta["tool_calls"] = len(resp.tool_calls)

            if not resp.tool_calls:
                return Reply(text=resp.content, tool_calls=calls_made)

            messages.append({"role": "assistant", **resp.raw_message})
            for call in resp.tool_calls:
                with trace.stage(f"tool:{call.name}") as meta:
                    result = self.tools.call(call.name, call.arguments)
                    meta["ok"] = result.ok
                calls_made.append({"name": call.name, "arguments": call.arguments, "ok": result.ok})
                content = wrap_untrusted(call.name, result.content)
                messages.append({"role": "tool", "tool_name": call.name, "content": content})

        return Reply(text="", tool_calls=calls_made)  # unreachable: last round has no tools
