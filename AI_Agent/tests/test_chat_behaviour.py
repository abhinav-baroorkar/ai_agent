"""From the owner's first test of the window: after Stop, "continue" lost the request,
and the clock was called when nobody asked. Fakes only; the model's own behaviour is on
the live check list (Dependencies/team/live-checks.md)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sasha.config import Settings
from sasha.orchestrator import Assistant, RequestFailed, TurnHooks
from sasha.pipeline.llm import LLMCancelled, LLMResponse, Message
from sasha.telemetry import TelemetryWriter
from sasha.tools import ToolRegistry
from sasha.tools.clock import clock_tool
from tests.server_fakes import WS, FakeLLM, FakeTTS, Harness, resp
from tests.wake_fakes import frames

POEM = "Twinkle twinkle little star how I wonder what you are"


class StoppedLLM(FakeLLM):
    """The first answer streams a few words, then waits for Stop and is cancelled."""

    def __init__(self, words: int = 3) -> None:
        super().__init__("big-model")
        self.words = words
        self.calls = 0
        self.streaming = threading.Event()

    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None,
        on_delta: Callable[[str], None],
        should_stop: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        if self.calls > 1:
            return super().chat_stream(messages, tools, on_delta, should_stop)
        self.seen.append((list(messages), tools is not None))
        for i, word in enumerate(POEM.split()[: self.words]):
            on_delta(word if i == 0 else f" {word}")
        self.streaming.set()
        while not (should_stop and should_stop()):
            threading.Event().wait(0.01)
        raise LLMCancelled


def assistant(settings: Settings, tmp: Path, llm: FakeLLM) -> Assistant:
    telemetry = TelemetryWriter(tmp / "telemetry", "a", "p")
    return Assistant(settings, llm, ToolRegistry(), telemetry)


def history_of(llm: FakeLLM) -> list[tuple[str, str]]:
    return [(m["role"], str(m["content"])) for m in llm.seen[-1][0] if m["role"] != "system"]


def test_stopped_turn_stays_in_history(settings: Settings, tmp_path: Path) -> None:
    llm = StoppedLLM()
    a = assistant(settings, tmp_path, llm)
    stop = threading.Event()
    shown: list[str] = []
    hooks = TurnHooks(on_delta=shown.append, should_stop=stop.is_set)
    worker = threading.Thread(target=lambda: _expect_stop(a, hooks))
    worker.start()
    assert llm.streaming.wait(5)
    stop.set()  # the owner pressed Stop
    worker.join(5)
    a.handle_text("continue")
    note = settings.profile.llm.interrupted_note
    assert history_of(llm) == [
        ("user", "Recite a poem"),
        ("assistant", f"Twinkle twinkle little {note}"),
        ("user", "continue"),
    ]
    assert "".join(shown) == "Twinkle twinkle little"  # the note was never shown


def _expect_stop(a: Assistant, hooks: TurnHooks) -> None:
    try:
        a.handle_text("Recite a poem", hooks)
    except RequestFailed as failed:
        assert isinstance(failed.__cause__, LLMCancelled)


def test_kept_partial_text_is_capped_and_kept_out_of_telemetry(
    settings: Settings, tmp_path: Path
) -> None:
    llm_cfg = settings.profile.llm.model_copy(update={"interrupted_keep_chars": 10})
    profile = settings.profile.model_copy(update={"llm": llm_cfg})
    capped = settings.model_copy(update={"profile": profile})
    llm = StoppedLLM(words=8)
    a = assistant(capped, tmp_path, llm)
    stop = threading.Event()
    stop.set()  # stopped as soon as the first words were out
    _expect_stop(a, TurnHooks(on_delta=lambda piece: None, should_stop=stop.is_set))
    a.handle_text("go on")
    kept = history_of(llm)[1][1]
    assert kept.startswith("Twinkle tw") and "little" not in kept
    written = "".join(p.read_text() for p in (tmp_path / "telemetry").glob("*.jsonl"))
    assert "LLMCancelled" in written and "Twinkle" not in written  # a flag, not the text


def test_cancel_mid_stream_on_the_socket_then_continue(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path)
    llm = StoppedLLM()
    h.llms["big-model"] = llm
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "Recite a poem"})
        assert llm.streaming.wait(5)
        ws.send_json({"type": "cancel"})
        while ws.receive_json() != {"type": "state", "state": "idle"}:
            pass
        ws.send_json({"type": "text", "text": "continue"})
        while ws.receive_json() != {"type": "state", "state": "idle"}:
            pass
    roles = [role for role, _ in history_of(llm)]
    assert roles == ["user", "assistant", "user"]
    assert history_of(llm)[1][1].startswith("Twinkle twinkle little")


def test_speech_cut_after_the_reply_marks_it(settings: Settings, tmp_path: Path) -> None:
    """The reply was finished but the owner stopped (or talked over) its speech."""
    tts = FakeTTS()
    tts.hold.set()
    h = Harness(settings, tmp_path, tts=tts)
    h.llm("big-model").responses = [resp("One line here. Two lines here. Three"), resp("ok")]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "Recite a poem"})
        frames(ws, "reply")
        ws.send_json({"type": "cancel"})
        tts.release.set()
        frames(ws, "state", "idle")
        tts.hold.clear()
        ws.send_json({"type": "text", "text": "continue"})
        frames(ws, "state", "idle")
    previous = history_of(h.llm("big-model"))[1][1]
    assert previous.endswith(settings.profile.llm.interrupted_note)


def test_clock_is_for_when_the_time_is_asked() -> None:
    description = clock_tool().description
    assert "only when" in description and "don't mention the time" in description.lower()
