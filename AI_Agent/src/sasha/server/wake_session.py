"""The listener socket (`/ws?wake=1`): every speech segment is a wake check.

Order per segment (design sections 2 to 5):
1. dropped before any transcription when the listener is paused, a turn is running,
   any socket is speaking (plus tail), or the cool-down after a turn is on;
2. transcribed, then `WakeGate.decide`;
3. on anything but a hit the transcript is discarded here, like the audio: it is
   never logged, never in telemetry, never sent to the LLM or the client (only
   `wake{heard:false}`). "Dropped" is structural: this path calls the Assistant only
   through `_turn`, and only with a hit's command.
Telemetry gets one line per check with labels and timings, never text. Segments,
the armed window and the telemetry lines are in server/wake_segment.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from starlette.websockets import WebSocket

from sasha.config import ReplyLines, WakeConfig
from sasha.conversation import current_turn
from sasha.orchestrator import Assistant, Reply
from sasha.server.speaking import ListenerPresence
from sasha.server.speech import Speaker
from sasha.server.timers.hub import Hub
from sasha.server.timers.ring import StopWindow
from sasha.server.timers.tools import TIMER_TOOLS, add_timer_tools
from sasha.server.voice import MIC_SAMPLE_RATE, VoiceStages
from sasha.server.wake import WakeGate
from sasha.server.wake_confirm import ConfirmFlow, PlaybackClock
from sasha.server.wake_policy import RateLimiter, WakeToolPolicy
from sasha.server.wake_segment import BYTES_PER_S, WakeSegments
from sasha.server.wake_truth import TruthfulReply
from sasha.server.ws import Frame
from sasha.telemetry import TelemetryWriter

log = logging.getLogger(__name__)
RELOAD_MS = 1000.0  # an LLM stage with load_ms above this means Ollama reloaded the model


def _reloaded(reply: Reply) -> bool:
    stages = reply.telemetry.get("stages", [])
    return any(float(s.get("meta", {}).get("load_ms") or 0) > RELOAD_MS for s in stages)


class ListenerConversation(WakeSegments):
    """Opened by server/wake_open.py (`open_listener`)."""

    def __init__(
        self,
        ws: WebSocket,
        assistant: Assistant,
        voice: VoiceStages,
        language: str,
        config: WakeConfig,
        gate: WakeGate,
        limiter: RateLimiter,
        device_control: Callable[[], bool],
        telemetry: TelemetryWriter,
        presence: ListenerPresence,
        recheck_placement: Callable[[], None] = lambda: None,
        speak: bool = True,
        timers: Hub | None = None,
    ) -> None:
        super().__init__(ws, assistant, voice, speak=speak, language=language, timers=timers)
        self.ring_role = "listener" if speak else "tab"
        self.capture_paused = False  # last pause_capture / resume_capture sent
        self.timer_claims = False  # wake turns: TruthfulReply covers timer claims too
        self.stop_window = StopWindow(self, timers) if timers is not None else None
        self.config = config
        self.gate = gate
        self.telemetry = telemetry
        self.presence = presence
        self.recheck_placement = recheck_placement
        self.paused = True  # fail closed until the listener says it is listening
        self.wake_prompt: Callable[[], str] | None = None  # set by open_listener
        self.armed_timer: asyncio.TimerHandle | None = None
        self.turn_language = language
        self.truth: TruthfulReply | None = None  # set while a wake turn's reply streams
        self.playback = PlaybackClock()
        self.confirm = ConfirmFlow(self)
        self.policy = WakeToolPolicy(
            config, device_control, limiter, self.confirm.blocking, self.record_tool
        )
        tools = assistant.tools
        if timers is not None and any(n in tools for n in TIMER_TOOLS):  # D4: voice-made
            said = current_turn.get  # the owner's messages: wake commands, after the name
            tools = add_timer_tools(tools, timers.service, "wake", self.confirm.blocking, said)
        assistant.tools = self.policy.wrap(tools)

    def emit(self, frame: Frame) -> None:
        if isinstance(frame, bytes):
            self.playback.frame(len(frame))
        elif frame.get("type") == "tts_start":
            self.playback.tts_start(int(frame.get("sample_rate") or 1))
        super().emit(frame)

    def make_turn_speaker(self) -> Speaker | None:
        return self._make_speaker(self.turn_language)

    # --- lifecycle ---------------------------------------------------------------
    async def run(self) -> None:
        self.voice.speaking.subscribe(id(self), self._on_pause)
        self._background(self.recheck_placement)  # a (re)connect often follows a resume
        try:
            await super().run()
        finally:
            self.voice.speaking.unsubscribe(id(self))
            self.presence.set(id(self), False)
            self._disarm()
            self.confirm.cancel()
            if self.stop_window is not None:
                self.stop_window.close()

    def _background(self, fn: Callable[[], None]) -> None:
        async def run() -> None:
            try:
                await asyncio.to_thread(fn)
            except Exception as exc:  # a check must never break the socket
                log.warning("Placement check failed: %s", type(exc).__name__)

        asyncio.create_task(run())  # noqa: RUF006 - fire and forget, errors logged

    def ring_started(self, item_id: str) -> None:
        if self.stop_window is not None:
            self.stop_window.close()  # the next tone: the window before it ends

    def ring_stopped(self, item_id: str) -> None:
        super().ring_stopped(item_id)
        if self.stop_window is not None and self.stop_window.item_id == item_id:
            self.stop_window.close()  # T6: no window once its item has ended

    async def ring_played(self, item_id: str) -> None:
        if self.stop_window is not None:
            await self.stop_window.open_after(item_id)

    def _on_pause(self, pause: bool) -> None:
        self.capture_paused = pause
        self.emit(
            {"type": "pause_capture", "reason": "speaking"} if pause else {"type": "resume_capture"}
        )

    # --- frames --------------------------------------------------------------------
    async def _on_text_frame(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            msg = None
        kind = msg.get("type") if isinstance(msg, dict) else None
        if not isinstance(msg, dict) or kind in ("cancel", "reset") or not isinstance(kind, str):
            if kind in ("cancel", "reset"):
                self.confirm.cancel()  # a pending question counts as "no"
            await super()._on_text_frame(raw)  # errors and cancel/reset as on any socket
        elif kind == "listener_state":
            self.paused = msg.get("paused") is not False
            self.presence.set(id(self), not self.paused)
            if self.paused:
                self.audio = None
                self._disarm()
        elif kind == "listener_stats":
            drops = msg.get("gate_drops")
            if isinstance(drops, int) and 0 <= drops < 1_000_000:
                self._write({"kind": "wake_gate", "gate_drops": drops})
        elif kind == "text":
            self.error("This socket takes speech only")
        elif kind == "audio_start":
            self._on_wake_start(msg)
        elif kind == "audio_stop":
            self._on_audio_stop()
        else:
            await super()._on_text_frame(raw)

    async def _ask(self, text: str, input_mode: str) -> Reply | None:
        if self.wake_prompt is not None:
            self.assistant.system_prompt = self.wake_prompt()  # never stale (see open_listener)
        lines = self.config.reply_lines
        line_set = lines.get(self.turn_language) or lines.get("en") or ReplyLines()
        truth = self.truth = (
            TruthfulReply(line_set, self.policy, text) if self.config.truthful_replies else None
        )
        try:
            reply = await super()._ask(text, input_mode)
        finally:
            self.truth = None
        if reply is not None and truth is not None and not self.turn_replaced:  # R3
            tail, reply.text = truth.finish(reply.text)
            if tail:
                self._on_delta(tail)  # sent and spoken like any streamed text
            if truth.overridden:
                self.record_tool(truth.overridden)
        if reply is not None and _reloaded(reply):  # Ollama loaded the model again
            self._background(self.recheck_placement)
        return reply

    def _on_delta_threadsafe(self, piece: str) -> None:
        """Wake turns: only text the truthful-reply check lets through goes out.

        The check runs here, in the LLM worker thread, because that is where text and
        tool calls happen in order: a sentence written before a tool call must be judged
        before the call marks the turn as having attempted a device change.
        """
        truth = self.truth
        if truth is None:
            super()._on_delta_threadsafe(piece)
        elif released := truth.feed(piece):
            super()._on_delta_threadsafe(released)

    def _on_wake_start(self, msg: dict[str, Any]) -> None:
        if self.stt is None:
            self.error("Voice is not available: no speech-to-text stage is configured")
        elif msg.get("wake") is not True:
            self.error("Listener segments must say wake: true")
        elif self.paused:
            self.error("Listener is paused")
        elif msg.get("sample_rate") not in (None, MIC_SAMPLE_RATE):
            self.error(f"sample_rate must be {MIC_SAMPLE_RATE}")
        elif self.audio is not None:
            self.error("A segment is already open")
        else:
            self.audio = bytearray()

    def _on_audio(self, data: bytes) -> None:
        if self.audio is None:
            return
        if len(self.audio) + len(data) > self.config.max_segment_s * BYTES_PER_S:
            self.audio = None
            self.error("Segment too long; dropped")
            return
        self.audio += data

    def _on_audio_stop(self) -> None:
        if self.audio is None:
            return
        audio, self.audio = bytes(self.audio), None
        if self.confirm.offer(audio):  # the answer to a pending question
            return
        stop_for = self.stop_window.offer(audio) if self.stop_window is not None else None
        if stop_for is not None:  # a segment in the stop window (D1)
            self._start(self._segment(audio, stop_for))
            return
        reason = self._drop_reason()
        if reason is not None:
            self._miss(reason, len(audio))
            return
        self._start(self._segment(audio))
