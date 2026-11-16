"""Runs one request through the stages. Phase 0: text in -> LLM + tools -> text out."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from sasha.config import Settings
from sasha.conversation import asks_back, current_turn, finish_turn, new_turn
from sasha.pipeline.llm import LLM, LLMCancelled, LLMResponse, Message, StreamingLLM
from sasha.said import DEVICE_TOOL, said_by_owner
from sasha.sites import add_sites
from sasha.telemetry import RequestTrace, TelemetryWriter
from sasha.tools import ToolRegistry
from sasha.tools.base import ToolResult

# Review P1: while outside text (a `browse` result) is in the conversation, a changing
# tool must be named by the owner's current message. (tool, arguments, owner's message,
# his earlier recent messages) -> did he say it? Built per bot (sasha/said.py); without
# one, every such call is refused. `earlier` is built here, never by the model: the
# owner's messages of the last 2 kept turns that ended within `llm.target_window_s`
# (a target like "the bedroom light" may come from there; the action never does).
ChangeGuard = Callable[[str, dict[str, Any], str, str], bool]
EARLIER_TURNS = 2
SAME_TURN = "Changes can't follow a web lookup in the same request; ask the owner to say it again"
NAME_IT = "Say which device and what to do, and I'll do it."
SAY_IT = "Say in your own words what to change, and I'll do it."  # not a device (G4)


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
    # Sites the turn's web results came from: the server's own `site`/`sites` fields of
    # tainting tools (browse: the tabs' record, P4), never the model's words.
    sites: list[str] = field(default_factory=list)


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


@dataclass
class _Turn:
    """One kept turn's record, alongside its two history entries (review E4): when it
    started and ended (the turn clock), and whether it brought in outside text."""

    started: float
    ended: float
    browsed: bool


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
        self._history: list[Message] = []
        self._turns: list[_Turn] = []  # per kept turn, filtered with `_history` (E4)
        # forget_turns may come from another thread (the Memory screen) mid-turn.
        self._history_lock = threading.Lock()
        # The latest forgotten window (start, when it was forgotten): a turn that started
        # inside it but ended after the forget is dropped when it lands (memory W2).
        self._forgot: tuple[float, float] | None = None
        self._this_turn: tuple[Message, Message] | None = None  # this turn's two entries
        # A section rebuilt for every turn after the system prompt (memory/prompt.py).
        self.prompt_extra: Callable[[], str] | None = None
        self._turn_browsed = False
        self.last_tool_calls: list[dict[str, Any]] = []
        self._last_turn_end: float | None = None
        self.turn_kept = False  # the last turn is in the history (see replace_reply)

    @property
    def name(self) -> str:
        return self.settings.assistant.name

    def previous_reply_asked(self) -> bool:
        """Did the last reply ask the owner something? Only the fact. A question whose
        speech was cut isn't counted: the answer is read alone and asks again (the safe
        failure, by design)."""
        replies = [m["content"] for m in self._history if m["role"] == "assistant"]
        last = str(replies[-1]) if replies else ""
        return asks_back(last) and not last.endswith(self.settings.profile.llm.interrupted_note)

    def previous_request(self) -> str | None:
        """The owner's previous message in this conversation (None at the start)."""
        said = [m["content"] for m in self._history if m["role"] == "user"]
        return str(said[-1]) if said else None

    def since_previous(self) -> float | None:
        """Seconds since the previous turn ended (None at the start)."""
        return None if self._last_turn_end is None else self.clock() - self._last_turn_end

    def reset(self) -> None:
        with self._history_lock:
            self._history.clear()
            self._turns.clear()
            self._this_turn = None
        self._last_turn_end = None

    @property
    def tainted(self) -> bool:
        """A turn still in the model's context brought in outside text (P1)."""
        recent = self._turns[-self.history_turns :] if self.history_turns > 0 else []
        return any(t.browsed for t in recent)

    def forget_turns(self, seconds: float) -> int:
        """Drop this conversation's turns that started in the last `seconds` (memory's
        forget(minutes)): the model can't quote them any more. A turn still running is
        dropped when it lands (W2). Returns how many went now."""
        now = self.clock()
        since = now - seconds
        with self._history_lock:
            keep = [i for i, t in enumerate(self._turns) if t.started < since]
            gone = len(self._turns) - len(keep)
            self._history = [m for i in keep for m in self._history[2 * i : 2 * i + 2]]
            self._turns = [self._turns[i] for i in keep]
            self._forgot = (since, now)
        return gone

    def _kept(self, entry: Message) -> bool:
        return any(m is entry for m in self._history)

    def redact_request(self, redact: Callable[[str], str], count: int = 1) -> None:
        """Rewrite the owner's last `count` messages as kept in the history (a refused
        secret, memory M7: this turn's, and the asked-back one before it). By identity,
        under the lock: never another turn's entry (W3)."""
        with self._history_lock:
            if self._this_turn is None or not self._kept(self._this_turn[0]):
                return
            at = next(i for i, m in enumerate(self._history) if m is self._this_turn[0])
            users = [m for m in self._history[: at + 1] if m["role"] == "user"][-count:]
            for message in users:
                message["content"] = redact(str(message["content"]))

    def _keep(self, text: str, reply: str, started: float) -> None:
        """Keep this turn in the history, unless a forget covered its start (W2)."""
        with self._history_lock:
            self._last_turn_end = ended = self.clock()
            if self._forgot is not None and self._forgot[0] <= started <= self._forgot[1]:
                self._this_turn, self.turn_kept = None, False
                return
            pair: tuple[Message, Message] = (
                {"role": "user", "content": text},
                {"role": "assistant", "content": reply},
            )
            self._history += list(pair)
            self._turns.append(_Turn(started, ended, self._turn_browsed))
            self._this_turn, self.turn_kept = pair, True

    # --- stopped turns ---------------------------------------------------------------
    # A stopped turn (the Stop button, or the owner talking over the reply) stays in the
    # history: the request, what was said of the reply (capped), and a short note only
    # the model sees. So "continue" or "go on" picks it up. Telemetry only records that
    # it was cancelled, never the text.
    def _stopped_reply(self, partial: str) -> str:
        cfg = self.settings.profile.llm
        kept = partial.strip()[: cfg.interrupted_keep_chars]
        return f"{kept} {cfg.interrupted_note}".strip()

    def _keep_stopped(self, text: str, partial: str, started: float) -> None:
        self._keep(text, self._stopped_reply(partial), started)

    def replace_reply(self, said: str, stopped: bool = False) -> None:
        """The history keeps what the person was really shown and told, not the model's
        raw text: the socket's released text (a held-back false claim, markdown taken
        out of speech). Only this turn's entry: nothing if the turn wasn't kept."""
        with self._history_lock:  # by identity: never another turn's entry (W3)
            if self.turn_kept and self._this_turn and self._kept(self._this_turn[1]):
                self._this_turn[1]["content"] = self._stopped_reply(said) if stopped else said

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
                    hooks.on_tool(call.name, result.ok)
                content = wrap_untrusted(call.name, result.content)
                messages.append({"role": "tool", "tool_name": call.name, "content": content})

        return Reply(text="", tool_calls=calls_made)  # unreachable: last round has no tools

    def _taints(self, name: str) -> bool:
        return name in self.tools and self.tools.get(name).taints

    def earlier_owner_text(self, turns: int = EARLIER_TURNS) -> str:
        """The owner's messages of the last `turns` kept turns that ended within
        `llm.target_window_s` (for the said-check's target words; see ChangeGuard)."""
        window = self.settings.profile.llm.target_window_s
        now = self.clock()
        with self._history_lock:  # one record per kept turn, filtered together (E4)
            users = [m for m in self._history if m["role"] == "user"]
            pairs = list(zip(users, self._turns, strict=True))
        recent = pairs[-turns:] if turns > 0 else []
        return " ".join(str(m["content"]) for m, t in recent if now - t.ended <= window)

    def _change_refused(self, name: str, arguments: dict[str, Any], text: str) -> ToolResult | None:
        """P1/P3 (and M1 with `always_guard`): a tool not marked read-only, while outside
        text is in the context, must be said by the owner."""
        if name not in self.tools:
            return None
        tool = self.tools.get(name)
        if tool.read_only:
            return None
        if self._turn_browsed:  # the stricter first layer: never in the same turn
            message = SAME_TURN
        elif not (self.tainted or self.always_guard):
            return None
        elif tool.said_checked and not self.tainted:
            return None  # its own said-check covers memory's M1
        elif tool.said_checked:
            # Under a web-result taint (P1, review E3) its own check covers times, not
            # the free text a page could choose: every word of its `said_fields` must
            # also be the owner's, this turn or the previous one within the window.
            own = f"{text} {self.earlier_owner_text(turns=1)}"
            if all(said_by_owner(str(arguments.get(f) or ""), own) for f in tool.said_fields):
                return None
            message = SAY_IT
        else:
            guard = self.change_guard
            earlier = "" if self.tainted else self.earlier_owner_text()  # P1 stays strict
            if guard is not None and guard(name, arguments, text, earlier):
                return None
            message = NAME_IT if name == DEVICE_TOOL else SAY_IT
        return ToolResult(ok=False, content=json.dumps({"error": message}, ensure_ascii=False))
