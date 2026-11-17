"""The listener's speech segments: decode, judge, arm the window, and the telemetry
line per check (split from server/wake_session.py, which holds the frames and turns).

A segment's transcript lives only inside `_segment`/`_decide`: on anything but a hit
it is discarded here; telemetry gets labels and numbers, never text.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sasha.config import WakeConfig
from sasha.pipeline.stt import Transcript
from sasha.server.timers.ring import StopWindow
from sasha.server.voice import MIC_SAMPLE_RATE
from sasha.server.wake import REJECTED_UNCONFIRMED, WakeGate
from sasha.server.wake_evidence import hear
from sasha.server.wake_policy import WakeToolPolicy
from sasha.server.ws import Conversation
from sasha.telemetry import TelemetryWriter

log = logging.getLogger(__name__)
BYTES_PER_S = MIC_SAMPLE_RATE * 2


class WakeSegments(Conversation):
    """The segment side of ListenerConversation; it sets these in its __init__."""

    gate: WakeGate
    config: WakeConfig
    stop_window: StopWindow | None
    telemetry: TelemetryWriter
    armed_timer: asyncio.TimerHandle | None
    turn_language: str
    policy: WakeToolPolicy
    paused: bool

    def _drop_reason(self, own_segment: bool = False) -> str | None:
        """`own_segment`: asked from inside a segment's task, which is the busy one."""
        if self.paused:
            return "dropped_paused"
        if self.busy and not own_segment:
            return "dropped_busy"
        if self.voice.speaking.speaking():
            return "dropped_speaking"
        if self.voice.speaking.in_cooldown():
            return "cooldown_drop"
        return None

    # --- one segment -------------------------------------------------------------------
    async def _segment(self, audio: bytes, stop_for: str | None = None) -> None:
        assert self.stt is not None
        queued = time.perf_counter()
        segment_bytes = len(audio)
        # The phrase as a hint, without which a short phrase comes out as anything. Not on
        # the armed follow-up (the command after the phrase alone): there the hint could
        # write the phrase into the command (review H1).
        hotwords = None if self.gate.armed else self.gate.matcher.phrase
        try:  # a doubtful wake is decoded again without the hint (false wakes, 2026-11-16)
            heard, plain, started = await hear(
                self.stt,
                self.voice.stt_lock,
                audio,
                MIC_SAMPLE_RATE,
                hotwords,
                self.gate,
                recheck=stop_for is None,  # a stop word is judged at once (review nit)
            )
        except Exception as exc:
            log.error("Speech recognition failed: %s", type(exc).__name__)
            self._miss("stt_error", segment_bytes)
            return
        finally:
            del audio
        done = time.perf_counter()
        window = self.stop_window
        if stop_for is not None and window is not None:
            if window.judge(stop_for, heard):
                return  # a stop word in the stop window: handled, the text dropped
            if reason := self._drop_reason(own_segment=True):  # T7: as any other segment
                self._miss(reason, segment_bytes)
                return
        stt_times = {
            "stt_ms": round((done - started) * 1000, 1),  # decoding only
            "stt_wait_ms": round((started - queued) * 1000, 1),  # queued behind others
        }
        await self._decide(heard, segment_bytes, stt_times, plain)

    async def _decide(
        self,
        heard: Transcript,
        segment_bytes: int,
        stt_times: dict[str, float],
        plain: str | None = None,
    ) -> None:
        assert self.stt is not None
        seconds = segment_bytes / BYTES_PER_S
        decision = self.gate.decide(heard.text, heard.confidence, heard.words, seconds, plain)
        language = heard.language or self.language
        confidence = heard.confidence
        del heard, plain  # the transcripts live no longer than this decision
        timings: dict[str, Any] = {
            **stt_times,
            "stt_device": self.stt.model_version(),
            "variant": decision.variant,
            # numbers only, never the text: how sure the decoder was, and the bar
            "confidence": None if confidence is None else round(confidence, 3),
            "min_confidence": self.gate.min_confidence,
            "phrase_p": decision.phrase_p,  # the name's word probability (wake_evidence)
            "rechecked": decision.rechecked or decision.outcome == REJECTED_UNCONFIRMED,
            "min_command_confidence": self.gate.min_command_confidence,
            "sure_command_confidence": self.gate.sure_command_confidence,
        }
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
        self.turn_language = language
        await self._turn(command, language, "wake")

    def _miss(self, outcome: str, segment_bytes: int, extra: dict[str, Any] | None = None) -> None:
        # `reason` is the outcome label only (never text), so the listener can say why
        self.emit({"type": "wake", "heard": False, "command": False, "reason": outcome})
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

    def record_tool(self, decision: str) -> None:
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
