"""One WebSocket conversation (`/ws`): text and voice turns, streaming, cancel, reset.

The Assistant, STT and TTS all block, so they run in worker threads. Frames from
those threads go through one outbox queue, and a single writer task writes them, so
frames never interleave. `cancel` sets an event the worker checks between stream
chunks, before every tool call and between speech sentences.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import Iterator
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from sasha.orchestrator import Assistant, Reply, RequestFailed, TurnHooks
from sasha.pipeline.llm import LLMCancelled, OllamaError
from sasha.pipeline.stt import SpeechToText
from sasha.pipeline.tts import SpeechAudio, TextToSpeech
from sasha.server.voice import MIC_SAMPLE_RATE, max_recording_bytes, pcm_frames

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
        stt: SpeechToText | None,
        tts: TextToSpeech | None,
    ) -> None:
        self.ws = ws
        self.assistant = assistant
        self.stt = stt
        self.tts = tts
        self.outbox: asyncio.Queue[Frame | None] = asyncio.Queue()
        self.stop = threading.Event()
        self.turn: asyncio.Task[None] | None = None
        self.audio: bytearray | None = None
        self.loop = asyncio.get_running_loop()

    # --- sending -------------------------------------------------------------
    def emit(self, frame: Frame) -> None:
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
            if self.turn is not None:
                self.turn.cancel()
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
        self._start(self._turn(text.strip(), speak_language=None))

    async def _turn(self, text: str, speak_language: str | None) -> None:
        try:
            self.state("thinking")
            reply = await self._ask(text, "voice" if speak_language else "text")
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
                if speak_language and self.tts and reply.text and not self.stop.is_set():
                    await self._speak(reply.text, speak_language)
        except Exception as exc:  # never let one turn kill the connection
            log.error("Turn failed: %s", type(exc).__name__)
            self.error(f"Something went wrong ({type(exc).__name__})")
        finally:
            self.state("idle")

    async def _ask(self, text: str, input_mode: str) -> Reply | None:
        hooks = TurnHooks(
            on_delta=lambda piece: self.emit_threadsafe({"type": "reply_delta", "text": piece}),
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
        await self._turn(heard.text.strip(), speak_language=heard.language)

    async def _speak(self, text: str, language: str) -> None:
        assert self.tts is not None
        self.state("speaking")
        chunks: Iterator[SpeechAudio] = await asyncio.to_thread(self.tts.stream, text, language)
        started = False
        try:
            while not self.stop.is_set():
                chunk = await asyncio.to_thread(next, chunks, None)
                if chunk is None or self.stop.is_set():
                    break
                if not started:
                    self.emit({"type": "tts_start", "sample_rate": chunk.sample_rate})
                    started = True
                for frame in pcm_frames(chunk.pcm, chunk.sample_rate):
                    self.emit(frame)
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(close)
            if started:
                self.emit({"type": "tts_end"})
