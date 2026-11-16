"""Runs one request through the stages. Phase 0: text in -> LLM + tools -> text out."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from sasha.config import Settings
from sasha.conversation import current_turn, finish_turn, new_turn
from sasha.pipeline.llm import LLM, LLMCancelled, LLMResponse, Message, StreamingLLM
from sasha.telemetry import RequestTrace, TelemetryWriter
from sasha.tools import ToolRegistry
from sasha.tools.base import ToolResult

# Review P1: while outside text (a `browse` result) is in the conversation, a changing
# tool must be named by the owner's current message. (tool, arguments, owner's message)
# -> did he say it? Built per bot (sasha/said.py); without one, every such call is refused.
ChangeGuard = Callable[[str, dict[str, Any], str], bool]
SAME_TURN = "Changes can't follow a web lookup in the same request; ask the owner to say it again"
NAME_IT = "Say which device and what to do, and I'll do it."


@dataclass
class TurnHooks:
    """Optional callbacks for one request (the server uses them; the CLI doesn't).

    `on_delta` gets answer text as it streams, `on_tool(name, ok)` fires after each tool,
    and `should_stop()` returning true cancels the request (raises `LLMCancelled`).
    """

    on_delta: Callable[[str], None] | None = None
    on_tool: Callable[[str, bool], None] | None = None
    should_stop: Callable[[], bool] | None = None


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
        system_prompt: str | None = None,
        max_rounds: int | None = None,
        change_guard: ChangeGuard | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.tools = tools
        self.telemetry = telemetry
        self.history_turns = history_turns
        self.system_prompt = system_prompt or settings.assistant.render_system_prompt()
        self.max_rounds = max_rounds  # None: the profile's max_tool_rounds
        self.change_guard = change_guard
        self._history: list[Message] = []
        self._browsed: list[bool] = []  # per kept turn: did it bring in outside text?
        self._turn_browsed = False
        self.last_tool_calls: list[dict[str, Any]] = []
        self.turn_kept = False  # the last turn is in the history (see replace_reply)

    @property
    def name(self) -> str:
        return self.settings.assistant.name

    def reset(self) -> None:
        self._history.clear()
        self._browsed.clear()

    @property
    def tainted(self) -> bool:
        """A turn still in the model's context brought in outside text (P1)."""
        return self.history_turns > 0 and any(self._browsed[-self.history_turns :])

    def _keep(self, text: str, reply: str) -> None:
        self._history += [
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply},
        ]
        self._browsed.append(self._turn_browsed)
        self.turn_kept = True

    # --- stopped turns ---------------------------------------------------------------
    # A stopped turn (the Stop button, or the owner talking over the reply) stays in the
    # history: the request, what was said of the reply (capped), and a short note only
    # the model sees. So "continue" or "go on" picks it up. Telemetry only records that
    # it was cancelled, never the text.
    def _stopped_reply(self, partial: str) -> str:
        cfg = self.settings.profile.llm
        kept = partial.strip()[: cfg.interrupted_keep_chars]
        return f"{kept} {cfg.interrupted_note}".strip()

    def _keep_stopped(self, text: str, partial: str) -> None:
        self._keep(text, self._stopped_reply(partial))

    def replace_reply(self, said: str, stopped: bool = False) -> None:
        """The history keeps what the person was really shown and told, not the model's
        raw text: the socket's released text (a held-back false claim, markdown taken
        out of speech). Only this turn's entry: nothing if the turn wasn't kept."""
        if self.turn_kept and self._history and self._history[-1]["role"] == "assistant":
            self._history[-1]["content"] = self._stopped_reply(said) if stopped else said

    def mark_interrupted(self) -> None:
        """The last reply was finished but its speech was cut: the model should know
        the person may not have heard all of it."""
        note = self.settings.profile.llm.interrupted_note
        if self._history and self._history[-1]["role"] == "assistant":
            last = self._history[-1]
            if not str(last["content"]).endswith(note):
                last["content"] = f"{str(last['content']).rstrip()} {note}"

    def _messages(self, text: str) -> list[Message]:
        recent = self._history[-2 * self.history_turns :] if self.history_turns > 0 else []
        return [
            {"role": "system", "content": self.system_prompt},
            *recent,
            {"role": "user", "content": text},
        ]

    def handle_text(
        self,
        text: str,
        hooks: TurnHooks | None = None,
        input_mode: str = "text",
        inherit_turn: bool = False,
    ) -> Reply:
        """One request. `inherit_turn` (the browse helper, design 10.2): run inside the
        current turn, so its tools check the owner's own messages, not `text`."""
        hooks = hooks or TurnHooks()
        self.turn_kept = False  # is this turn in the history (for replace_reply)?
        streamed: list[str] = []  # what reached the person so far, for a stopped turn
        if hooks.on_delta is not None:
            shown = hooks.on_delta

            def on_delta(piece: str) -> None:
                streamed.append(piece)
                shown(piece)

            hooks = replace(hooks, on_delta=on_delta)
        trace = self.telemetry.start(input_mode=input_mode)
        trace.models["llm"] = self.llm.model_version()
        trace.record_text("user", text)
        calls_made: list[dict[str, Any]] = []
        self.last_tool_calls = calls_made
        self._turn_browsed = False
        inherited = current_turn.get() if inherit_turn else None
        if inherit_turn and inherited is None:
            raise RuntimeError("inherit_turn needs a running turn")
        owner_said = (*(m["content"] for m in self._history if m["role"] == "user"), text)
        turn = inherited or new_turn(owner_said, input_mode, hooks.should_stop)
        token = current_turn.set(turn)  # for tools (B1)
        try:
            reply = self._run(text, trace, calls_made, hooks)
        except BaseException as exc:  # includes Ctrl-C: still write the telemetry line
            trace.finish(success=False, error=f"{type(exc).__name__}: {exc}")
            if isinstance(exc, LLMCancelled):  # stopped: keep it, so "continue" works
                self._keep_stopped(text, "".join(streamed))
            if isinstance(exc, Exception):
                raise RequestFailed(calls_made) from exc
            raise  # Ctrl-C, SystemExit: unchanged, so `except Exception` can't swallow them
        finally:
            current_turn.reset(token)
            if inherited is None:  # an inherited turn ends with its owner's
                finish_turn(turn.turn_id)  # e.g. the browser shows "idle" again
        trace.record_text("assistant", reply.text)
        reply.telemetry = trace.finish(success=True)
        self._keep(text, reply.text)
        return reply

    def _ask(
        self, messages: list[Message], tools: list[dict[str, Any]] | None, hooks: TurnHooks
    ) -> LLMResponse:
        if hooks.on_delta and isinstance(self.llm, StreamingLLM):
            return self.llm.chat_stream(messages, tools, hooks.on_delta, hooks.should_stop)
        return self.llm.chat(messages, tools=tools)

    def _run(
        self,
        text: str,
        trace: RequestTrace,
        calls_made: list[dict[str, Any]],
        hooks: TurnHooks,
    ) -> Reply:
        messages = self._messages(text)
        max_rounds = self.max_rounds or self.settings.profile.llm.max_tool_rounds
        specs = self.tools.specs() if len(self.tools) else None

        for round_no in range(max_rounds + 1):
            # On the last round, withhold tools so the model must answer in words.
            offer_tools = specs if round_no < max_rounds else None
            with trace.stage("llm", round=round_no, model=self.llm.model_id) as meta:
                resp = self._ask(messages, offer_tools, hooks)
                meta.update(resp.stats)
                meta["tool_calls"] = len(resp.tool_calls)

            if not resp.tool_calls:
                return Reply(text=resp.content, tool_calls=calls_made)

            messages.append({"role": "assistant", **resp.raw_message})
            for call in resp.tool_calls:
                if hooks.should_stop and hooks.should_stop():
                    raise LLMCancelled  # never run a tool after the user said stop
                with trace.stage(f"tool:{call.name}") as meta:
                    refused = self._change_refused(call.name, call.arguments, text)
                    result = refused or self.tools.call(call.name, call.arguments)
                    meta["ok"] = result.ok
                if result.ok and self._taints(call.name):
                    self._turn_browsed = True
                calls_made.append({"name": call.name, "arguments": call.arguments, "ok": result.ok})
                if hooks.on_tool:
                    hooks.on_tool(call.name, result.ok)
                content = wrap_untrusted(call.name, result.content)
                messages.append({"role": "tool", "tool_name": call.name, "content": content})

        return Reply(text="", tool_calls=calls_made)  # unreachable: last round has no tools

    def _taints(self, name: str) -> bool:
        return name in self.tools and self.tools.get(name).taints

    def _change_refused(self, name: str, arguments: dict[str, Any], text: str) -> ToolResult | None:
        """P1/P3: a tool not marked read-only, while outside text is in the context."""
        if name not in self.tools or self.tools.get(name).read_only:
            return None
        if self._turn_browsed:  # the stricter first layer: never in the same turn
            message = SAME_TURN
        else:
            guard = self.change_guard
            if not self.tainted or (guard is not None and guard(name, arguments, text)):
                return None
            message = NAME_IT
        return ToolResult(ok=False, content=json.dumps({"error": message}, ensure_ascii=False))
