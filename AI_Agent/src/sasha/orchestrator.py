"""Runs one request through the stages. Phase 0: text in -> LLM + tools -> text out.

The history side is sasha/history.py, the change guard sasha/change_guard.py and the
turn's types sasha/turn_types.py; their names are re-exported here."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from sasha.change_guard import NAME_IT, SAME_TURN, SAY_IT, ChangeGuard, GuardMixin
from sasha.config import Settings
from sasha.conversation import current_turn, finish_turn, new_turn
from sasha.history import EARLIER_TURNS
from sasha.pipeline.llm import LLM, LLMCancelled, LLMResponse, Message, StreamingLLM
from sasha.sites import add_sites
from sasha.telemetry import RequestTrace, TelemetryWriter
from sasha.tools import ToolRegistry
from sasha.turn_types import Reply, RequestFailed, TurnHooks, wrap_untrusted

__all__ = [
    "EARLIER_TURNS", "NAME_IT", "SAME_TURN", "SAY_IT", "Assistant", "ChangeGuard", "Reply",
    "RequestFailed", "TurnHooks", "wrap_untrusted",
]  # fmt: skip


class Assistant(GuardMixin):
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
        always_guard: bool = False,  # memory-on bots (M1): the guard on every turn
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.tools = tools
        self.telemetry = telemetry
        self.history_turns = history_turns
        self.system_prompt = system_prompt or settings.assistant.render_system_prompt()
        self.max_rounds = max_rounds  # None: the profile's max_tool_rounds
        self.change_guard = change_guard
        self.always_guard = always_guard
        self.clock = clock
        self._init_history()
        # A section rebuilt for every turn after the system prompt (memory/prompt.py).
        self.prompt_extra: Callable[[], str] | None = None
        self.last_tool_calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self.settings.assistant.name

    def _messages(self, text: str) -> list[Message]:
        recent = self._history[-2 * self.history_turns :] if self.history_turns > 0 else []
        extra = self.prompt_extra() if self.prompt_extra is not None else ""
        system = f"{self.system_prompt}\n\n{extra}" if extra else self.system_prompt
        return [
            {"role": "system", "content": system},
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
        started = self.clock()
        self.turn_kept = False  # is this turn in the history (for replace_reply)?
        self._this_turn = None
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
        asked, since = self.previous_reply_asked(), self.since_previous()
        turn = inherited or new_turn(owner_said, input_mode, asked, since, hooks.should_stop)
        token = current_turn.set(turn)  # for tools (B1; the timers' said-check)
        try:
            reply = self._run(text, trace, calls_made, hooks)
        except BaseException as exc:  # includes Ctrl-C: still write the telemetry line
            trace.finish(success=False, error=f"{type(exc).__name__}: {exc}")
            if isinstance(exc, LLMCancelled):  # stopped: keep it, so "continue" works
                self._keep_stopped(text, "".join(streamed), started)
            if isinstance(exc, Exception):
                raise RequestFailed(calls_made) from exc
            raise  # Ctrl-C, SystemExit: unchanged, so `except Exception` can't swallow them
        finally:
            current_turn.reset(token)
            if inherited is None:  # an inherited turn ends with its owner's
                finish_turn(turn.turn_id)  # e.g. the browser shows "idle" again
        trace.record_text("assistant", reply.text)
        reply.telemetry = trace.finish(success=True)
        self._keep(text, reply.text, started)
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
        sites: list[str] = []
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
                return Reply(text=resp.content, tool_calls=calls_made, sites=sites)

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
                    add_sites(sites, result.content)
                made = {"name": call.name, "arguments": call.arguments, "ok": result.ok}
                if result.asks:
                    made["asks"] = result.asks  # a reason label only (truth.py)
                if result.report:
                    made["report"] = result.report  # the tool's own summary (memory)
                calls_made.append(made)
                if hooks.on_tool:
                    hooks.on_tool(call.name, result.ok, result.asks)
                content = wrap_untrusted(call.name, result.content)
                messages.append({"role": "tool", "tool_name": call.name, "content": content})

        return Reply(text="", tool_calls=calls_made)  # unreachable: last round has no tools
