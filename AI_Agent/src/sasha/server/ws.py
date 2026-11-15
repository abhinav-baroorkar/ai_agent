"""One WebSocket conversation (`/ws`): text and voice turns, streaming, cancel, reset.

The Assistant, STT and TTS all block, so they run in worker threads. Frames from
those threads go through one outbox queue, and a single writer task writes them, so
frames never interleave. `cancel` sets an event the worker checks between stream
chunks, before every tool call and between speech chunks. Cancel and disconnect
therefore take effect at Ollama's next streamed line: a long prompt evaluation runs
until the first token arrives (the HTTP stream is closed then).

Each socket is its own conversation (own history), even for the same bot. With
`speak=1` every answer is also spoken while it streams, whatever started the turn,
if the TTS stage can speak the turn's language (see `speech.Speaker`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from sasha.orchestrator import Assistant, Reply, RequestFailed, TurnHooks
from sasha.pipeline.llm import LLMCancelled, OllamaError
from sasha.server.speech import Speaker
from sasha.server.timers.hub import Hub
from sasha.server.timers.ring import RingPlayer
from sasha.server.timers.service import TimerError
from sasha.server.voice import MIC_SAMPLE_RATE, VoiceStages, max_recording_bytes

log = logging.getLogger(__name__)
MAX_TEXT_CHARS = 8000
Frame = dict[str, Any] | bytes


def _timing(reply: Reply) -> dict[str, Any]:
    stages = reply.telemetry.get("stages", [])
    tps = [s["meta"]["tokens_per_s"] for s in stages if s["meta"].get("tokens_per_s")]
    return {
        "total_ms": reply.telemetry.get("total_ms", 0),
        "tokens_per_s": tps[-1] if tps else None,
    }


def _failure_message(failed: RequestFailed) -> str:
    cause = failed.__cause__
    if isinstance(cause, OllamaError):
        return str(cause)
    return f"Something went wrong ({type(cause).__name__})"


class Conversation:
    def __init__(
        self,
        ws: WebSocket,
        assistant: Assistant,
        voice: VoiceStages,
        speak: bool = False,
        language: str = "en",
        timers: Hub | None = None,
    ) -> None:
        self.ws = ws
        self.assistant = assistant
        self.voice = voice
        self.stt = voice.stt
        self.speak = speak
        self.language = language  # for typed turns; voice turns use the transcript's
        self.speaker: Speaker | None = None
        self.outbox: asyncio.Queue[Frame | None] = asyncio.Queue()
        self.stop = threading.Event()
        self.turn: asyncio.Task[None] | None = None
        self.audio: bytearray | None = None
        self.loop = asyncio.get_running_loop()
        # Timers (server/timers/): every socket gets the frames; a `speak=1` socket may
        # be the one that speaks a ring (the Home window first, then the listener).
        self.timers = timers
        self.ring_role = "home" if speak else "tab"
        self.ringer = RingPlayer(self, timers) if timers is not None else None

    # --- timers (the hub calls these from any thread) ------------------------------
    def push_threadsafe(self, frame: dict[str, Any]) -> None:
        self.emit_threadsafe(frame)

    def ring_threadsafe(self, item_id: str, say: str, tone: bool, check: bool = True) -> None:
        if self.ringer is not None:
            self.ringer.ring_threadsafe(item_id, say, tone, check)

    def stop_ring_threadsafe(self, item_id: str) -> None:
        with contextlib.suppress(RuntimeError):  # the loop is gone: nothing to stop
            self.loop.call_soon_threadsafe(self.ring_stopped, item_id)

    def ring_stopped(self, item_id: str) -> None:
        """The item ended: its ring speech stops (the listener also closes its window)."""
        if self.ringer is not None:
            self.ringer.stop(item_id)

    def ring_started(self, item_id: str) -> None:
        """A ring's speech is about to start on this socket (the listener overrides)."""

    async def ring_played(self, item_id: str) -> None:
        """A ring's speech was sent (the listener opens its stop window)."""

    async def _on_timer_frame(self, kind: str, msg: dict[str, Any]) -> None:
        if self.timers is None:
            self.error("Timers are not available")
            return
        service, item_id = self.timers.service, msg.get("id")
        minutes = msg.get("minutes", service.config.snooze_minutes)
        if not isinstance(item_id, str) or (
            kind == "timer_snooze" and (isinstance(minutes, bool) or not isinstance(minutes, int))
        ):
            self.error("Bad timer message")
            return
        try:
            if kind == "timer_dismiss":
                await asyncio.to_thread(service.dismiss, item_id)
            else:
                await asyncio.to_thread(service.snooze, item_id, minutes)
        except TimerError as exc:
            self.error(str(exc))

    # --- sending -------------------------------------------------------------
    def emit(self, frame: Frame) -> None:
        if isinstance(frame, dict):  # brain-wide speaking state, for the wake listener
            if frame.get("type") == "tts_start":
                self.voice.speaking.started(id(self))
            elif frame.get("type") == "tts_end":
                self.voice.speaking.ended(id(self))
        self.outbox.put_nowait(frame)

    def emit_threadsafe(self, frame: Frame) -> None:
        self.loop.call_soon_threadsafe(self.outbox.put_nowait, frame)

    def state(self, value: str) -> None:
        self.emit({"type": "state", "state": value})

    def error(self, message: str) -> None:
        self.emit({"type": "error", "message": message})

    async def _writer(self) -> None:
        while (frame := await self.outbox.get()) is not None:
            try:
                if isinstance(frame, bytes):
                    await self.ws.send_bytes(frame)
                else:
                    await self.ws.send_text(json.dumps(frame, ensure_ascii=False))
            except (WebSocketDisconnect, RuntimeError):
                return  # client went away; the receive loop will notice too

    # --- main loop -----------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.turn is not None and not self.turn.done()

    async def run(self) -> None:
        writer = asyncio.create_task(self._writer())
        self.state("idle")
        if self.timers is not None:
            self.timers.add(self)  # the `timers` snapshot, and any missed-items line
        try:
            while True:
                message = await self.ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    self._on_audio(message["bytes"])
                elif message.get("text") is not None:
                    await self._on_text_frame(message["text"])
        except WebSocketDisconnect:
            pass
        finally:
            self.stop.set()  # let a running worker thread stop at its next check
            if self.speaker is not None:
                self.speaker.cancel()
            if self.turn is not None:
                self.turn.cancel()
            if self.timers is not None:
                self.timers.remove(self)
            if self.ringer is not None:
                self.ringer.close()
            self.voice.speaking.ended(id(self))  # in case speech was cut before tts_end
            self.outbox.put_nowait(None)
            with contextlib.suppress(asyncio.CancelledError):
                await writer

    async def _on_text_frame(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            msg = None
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            self.error("Bad message: expected a JSON object with a 'type'")
            return
        kind = msg["type"]
        if kind == "text":
            self._on_text(msg.get("text"))
        elif kind == "cancel":
            await self._cancel()
        elif kind == "reset":
            await self._cancel()
            self.assistant.reset()
            self.state("idle")
        elif kind == "audio_start":
            self._on_audio_start(msg.get("sample_rate"))
        elif kind == "audio_stop":
            self._on_audio_stop()
        elif kind in ("timer_dismiss", "timer_snooze"):
            await self._on_timer_frame(kind, msg)
        else:
            self.error(f"Unknown message type '{kind[:40]}'")

    async def _cancel(self) -> None:
        if self.audio is not None:
            self.audio = None
            self.state("idle")
        if self.busy:
            self.stop.set()
            assert self.turn is not None
            with contextlib.suppress(asyncio.CancelledError):
                await self.turn

    def _start(self, coro: Any) -> None:
        self.stop.clear()
        self.turn = asyncio.create_task(coro)

    # --- text ------------------------------------------------------------------
    def _on_text(self, text: Any) -> None:
        if self.busy or self.audio is not None:
            self.error("Busy: wait for the reply or send cancel")
            return
        if not isinstance(text, str) or not text.strip():
            self.error("Empty message")
            return
        if len(text) > MAX_TEXT_CHARS:
            self.error(f"Message too long (max {MAX_TEXT_CHARS} characters)")
            return
        self._start(self._turn(text.strip(), self.language, "text"))

    def _make_speaker(self, language: str) -> Speaker | None:
        tts = self.voice.tts
        if not self.speak or tts is None or language not in self.voice.languages:
            return None  # text only: no speech requested, no TTS, or language not spoken
        return Speaker(tts, language, self.voice.split, self.voice.tts_lock, self.emit, self.stop)

    def _on_delta(self, piece: str) -> None:  # on the event loop
        self.emit({"type": "reply_delta", "text": piece})
        if self.speaker is not None:
            self.speaker.feed(piece)

    def _on_delta_threadsafe(self, piece: str) -> None:  # from the LLM worker thread
        self.loop.call_soon_threadsafe(self._on_delta, piece)

    async def _turn(self, text: str, language: str, input_mode: str) -> None:
        # Kept in self.speaker until speech is over, so a disconnect can cancel it. A
        # wake confirmation may swap it mid-turn (server/wake_confirm.py), so the end of
        # the turn always uses the current one.
        self.speaker = self._make_speaker(language)
        spoken = False
        try:
            self.state("thinking")
            reply = await self._ask(text, input_mode)
            if reply is not None:
                calls = [{"name": c["name"], "ok": c["ok"]} for c in reply.tool_calls]
                self.emit(
                    {
                        "type": "reply",
                        "text": reply.text,
                        "tool_calls": calls,
                        "timing": _timing(reply),
                    }
                )
            if self.speaker is not None:
                speaker = self.speaker
                await (speaker.finish() if reply is not None else speaker.abort())
            spoken = True
        except Exception as exc:  # never let one turn kill the connection
            log.error("Turn failed: %s", type(exc).__name__)
            self.error(f"Something went wrong ({type(exc).__name__})")
        finally:
            if self.speaker is not None and not spoken:  # error or cancelled: stop now
                self.speaker.cancel()
            self.speaker = None
            self.voice.speaking.turn_ended()  # wake cool-down starts at any turn's end
            self.state("idle")

    async def _ask(self, text: str, input_mode: str) -> Reply | None:
        hooks = TurnHooks(
            on_delta=self._on_delta_threadsafe,
            on_tool=lambda name, ok: self.emit_threadsafe({"type": "tool", "name": name, "ok": ok}),
            should_stop=self.stop.is_set,
        )
        try:
            return await asyncio.to_thread(self.assistant.handle_text, text, hooks, input_mode)
        except RequestFailed as failed:
            if not isinstance(failed.__cause__, LLMCancelled):
                self.error(_failure_message(failed))
            return None

    # --- voice -------------------------------------------------------------------
    def _on_audio_start(self, sample_rate: Any) -> None:
        if self.stt is None:
            self.error("Voice is not available: no speech-to-text stage is configured")
            return
        if self.busy or self.audio is not None:
            self.error("Busy: wait for the reply or send cancel")
            return
        if sample_rate not in (None, MIC_SAMPLE_RATE):
            self.error(f"sample_rate must be {MIC_SAMPLE_RATE}")
            return
        self.audio = bytearray()
        self.state("listening")

    def _on_audio(self, data: bytes) -> None:
        if self.audio is None:
            return  # stray frames after cancel/stop: ignore
        if len(self.audio) + len(data) > max_recording_bytes():
            self.audio = None
            self.error("Recording too long; stopped")
            self.state("idle")
            return
        self.audio += data

    def _on_audio_stop(self) -> None:
        if self.audio is None:
            if self.stt is None:
                self.error("Voice is not available: no speech-to-text stage is configured")
            return  # already cancelled or never started
        audio, self.audio = bytes(self.audio), None
        self._start(self._voice_turn(audio))

    async def _voice_turn(self, audio: bytes) -> None:
        assert self.stt is not None
        self.state("thinking")
        try:
            async with self.voice.stt_lock:
                heard = await asyncio.to_thread(self.stt.transcribe, audio, MIC_SAMPLE_RATE)
        except Exception as exc:
            log.error("Speech recognition failed: %s", type(exc).__name__)
            self.error("Speech recognition failed")
            self.state("idle")
            return
        self.emit({"type": "transcript", "text": heard.text, "final": True})
        if not heard.text.strip() or self.stop.is_set():
            self.state("idle")
            return
        await self._turn(heard.text.strip(), heard.language or self.language, "voice")
