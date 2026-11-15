"""The listener socket (`/ws?wake=1`): every speech segment is a wake check.

Order per segment (design sections 2 to 5):
1. dropped before any transcription when the listener is paused, a turn is running,
   any socket is speaking (plus tail), or the cool-down after a turn is on;
2. transcribed, then `WakeGate.decide`;
3. on anything but a hit the transcript is discarded here, like the audio: it is
   never logged, never in telemetry, never sent to the LLM or the client (only
   `wake{heard:false}`). "Dropped" is structural: this path calls the Assistant only
   through `_turn`, and only with a hit's command.
Telemetry gets one line per check with labels and timings, never text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from starlette.websockets import WebSocket

from sasha.bots import VOICE_ID
from sasha.config import WakeConfig
from sasha.orchestrator import Assistant, Reply
from sasha.server.deps import ServerDeps
from sasha.server.speaking import ListenerPresence
from sasha.server.voice import MIC_SAMPLE_RATE, VoiceStages
from sasha.server.wake import WakeGate, WakeMatcher, is_yes
from sasha.server.wake_policy import RateLimiter, WakeToolPolicy
from sasha.server.ws import Conversation
from sasha.telemetry import TelemetryWriter

log = logging.getLogger(__name__)
BYTES_PER_S = MIC_SAMPLE_RATE * 2
CONFIRM_SPEECH_WAIT_S = 30.0  # longest we wait for the spoken question to finish
RELOAD_MS = 1000.0  # an LLM stage with load_ms above this means Ollama reloaded the model


def _reloaded(reply: Reply) -> bool:
    stages = reply.telemetry.get("stages", [])
    return any(float(s.get("meta", {}).get("load_ms") or 0) > RELOAD_MS for s in stages)


async def open_listener(
    ws: WebSocket, deps: ServerDeps, voice: VoiceStages, speak: bool
) -> ListenerConversation:
    """The listener's conversation: the `voice` bot, wrapped in the wake-turn policy."""
    settings = deps.settings
    bot = deps.bots.get(VOICE_ID)
    assistant, warnings = await asyncio.to_thread(deps.make_assistant, bot)
    for warning in warnings:
        log.warning("Bot %s: %s", bot.id, warning)
    wake_word, config = settings.assistant.wake_word, settings.profile.wake
    matcher = WakeMatcher(
        wake_word.phrase, wake_word.variants, config.fillers, config.continuations
    )
    assert deps.wake_limiter is not None
    return ListenerConversation(
        ws,
        assistant,
        voice,
        language=settings.assistant.languages.default,
        config=config,
        gate=WakeGate(matcher, config.armed_window_s, config.min_confidence),
        limiter=deps.wake_limiter,
        device_control=lambda: deps.bots.get(VOICE_ID).wake_device_control,
        telemetry=deps.telemetry,
        presence=deps.presence,
        recheck_placement=deps.recheck_placement,
        speak=speak,
    )


class ListenerConversation(Conversation):
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
    ) -> None:
        super().__init__(ws, assistant, voice, speak=speak, language=language)
        self.config = config
        self.gate = gate
        self.telemetry = telemetry
        self.presence = presence
        self.recheck_placement = recheck_placement
        self.paused = True  # fail closed until the listener says it is listening
        self.confirm_waiter: asyncio.Future[bytes] | None = None
        self.armed_timer: asyncio.TimerHandle | None = None
        self.policy = WakeToolPolicy(
            config, device_control, limiter, self._confirm_blocking, self._record_tool
        )
        assistant.tools = self.policy.wrap(assistant.tools)

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
            self._resolve_confirm(b"")

    def _background(self, fn: Callable[[], None]) -> None:
        async def run() -> None:
            try:
                await asyncio.to_thread(fn)
            except Exception as exc:  # a check must never break the socket
                log.warning("Placement check failed: %s", type(exc).__name__)

        asyncio.create_task(run())  # noqa: RUF006 - fire and forget, errors logged

    def _on_pause(self, pause: bool) -> None:
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
                self._resolve_confirm(b"")  # a pending question counts as "no"
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
        reply = await super()._ask(text, input_mode)
        if reply is not None and _reloaded(reply):  # Ollama loaded the model again
            self._background(self.recheck_placement)
        return reply

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
        if self.confirm_waiter is not None and not self.confirm_waiter.done():
            self.confirm_waiter.set_result(audio)  # the answer to a pending question
            return
        reason = self._drop_reason()
        if reason is not None:
            self._miss(reason, len(audio))
            return
        self._start(self._segment(audio))

    def _drop_reason(self) -> str | None:
        if self.paused:
            return "dropped_paused"
        if self.busy:
            return "dropped_busy"
        if self.voice.speaking.speaking():
            return "dropped_speaking"
        if self.voice.speaking.in_cooldown():
            return "cooldown_drop"
        return None

    # --- one segment -------------------------------------------------------------------
    async def _segment(self, audio: bytes) -> None:
        assert self.stt is not None
        started = time.perf_counter()
        segment_bytes = len(audio)
        try:
            async with self.voice.stt_lock:
                heard = await asyncio.to_thread(self.stt.transcribe, audio, MIC_SAMPLE_RATE)
        except Exception as exc:
            log.error("Speech recognition failed: %s", type(exc).__name__)
            self._miss("stt_error", segment_bytes)
            return
        finally:
            del audio
        stt_ms = (time.perf_counter() - started) * 1000
        decision = self.gate.decide(heard.text, heard.confidence)
        language = heard.language or self.language
        del heard  # the transcript lives no longer than this decision
        timings = {"stt_ms": round(stt_ms, 1), "variant": decision.variant}
        if not decision.heard:
            self._miss(decision.outcome, segment_bytes, timings)
            return
        if decision.armed:
            self.emit(
                {
                    "type": "wake",
                    "heard": True,
                    "command": False,
                    "armed_s": self.config.armed_window_s,
                }
            )
            self._arm()
            self._check(decision.outcome, segment_bytes, timings)
            return
        self._disarm()
        command = decision.command or ""
        self.emit({"type": "wake", "heard": True, "command": True})
        self._check(decision.outcome, segment_bytes, timings)
        self.emit({"type": "transcript", "text": command, "final": True})
        self.policy.begin_turn()
        await self._turn(command, language, "wake")

    def _miss(self, outcome: str, segment_bytes: int, extra: dict[str, Any] | None = None) -> None:
        self.emit({"type": "wake", "heard": False, "command": False})
        self._check(outcome, segment_bytes, extra or {})

    # --- armed window ---------------------------------------------------------------------
    def _arm(self) -> None:
        if self.armed_timer is not None:
            self.armed_timer.cancel()
        self.armed_timer = self.loop.call_later(self.config.armed_window_s + 0.05, self._expire)

    def _disarm(self) -> None:
        if self.armed_timer is not None:
            self.armed_timer.cancel()
            self.armed_timer = None
        self.gate.disarm()

    def _expire(self) -> None:
        self.armed_timer = None
        if self.gate.expired():
            self.audio = None  # an expired window drops what was being sent, like a miss
            self.emit({"type": "armed_expired"})
            self._check("followup_expired", 0, {})

    # --- confirmation (called from the LLM worker thread) --------------------------------
    def _confirm_blocking(self, action: str) -> bool:
        future = asyncio.run_coroutine_threadsafe(self._ask_confirm(action), self.loop)
        try:
            return bool(
                future.result(timeout=CONFIRM_SPEECH_WAIT_S + self.config.confirm_window_s + 5)
            )
        except Exception:
            future.cancel()
            return False

    async def _ask_confirm(self, action: str) -> bool:
        if self.stop.is_set() or self.stt is None:
            return False
        window = self.config.confirm_window_s
        self.emit({"type": "confirm", "action": action, "timeout_s": window})
        if self.speaker is not None:
            self.speaker.say(f"Should I {action}?")
            for _ in range(60):  # up to 3 s for the question's speech to begin
                if self.voice.speaking.speaking():
                    break
                await asyncio.sleep(0.05)
        await self.voice.speaking.wait_quiet(CONFIRM_SPEECH_WAIT_S)
        self.confirm_waiter = self.loop.create_future()
        try:
            audio = await asyncio.wait_for(self.confirm_waiter, window)
        except asyncio.TimeoutError:
            self._record_tool("confirm_timeout")
            return False
        finally:
            self.confirm_waiter = None
        if not audio:
            return False
        try:
            async with self.voice.stt_lock:
                answer = await asyncio.to_thread(self.stt.transcribe, audio, MIC_SAMPLE_RATE)
        except Exception:
            return False
        yes = is_yes(answer.text, self.config.yes_words)
        del answer, audio  # the answer's text is not kept either
        return yes

    def _resolve_confirm(self, audio: bytes) -> None:
        if self.confirm_waiter is not None and not self.confirm_waiter.done():
            self.confirm_waiter.set_result(audio)

    # --- telemetry (labels and numbers only) ---------------------------------------------
    def _check(self, outcome: str, segment_bytes: int, extra: dict[str, Any]) -> None:
        self._write(
            {
                "kind": "wake_check",
                "outcome": outcome,
                "segment_ms": round(segment_bytes / BYTES_PER_S * 1000),
                **extra,
            }
        )

    def _record_tool(self, decision: str) -> None:
        self._write({"kind": "wake_tool", "decision": decision})

    def _write(self, record: dict[str, Any]) -> None:
        if not self.telemetry.enabled:
            return
        base = {
            "schema": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "profile": self.telemetry.profile,
        }
        try:
            self.telemetry.write({**base, **record})
        except OSError as exc:
            log.warning("Wake telemetry not written: %s", type(exc).__name__)
