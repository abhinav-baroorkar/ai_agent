"""Spoken confirmation for wake turns: ask, wait for one segment, read it as yes or no.

Called from the LLM worker thread (the tool policy blocks on `blocking(action)`); the
work runs on the listener's event loop:
1. send `{type:"confirm", action, timeout_s}`;
2. speak the question as its own utterance: the turn's speech so far is finished
   first, the question gets its own `tts_start`/`tts_end`, and the rest of the turn
   gets a fresh speaker;
3. time the window from the estimated end of *playback* (S3): `tts_start` time plus
   the duration of the PCM sent, plus the tail. Frames go out faster than real time,
   so the sent `tts_end` is too early; the listener's own window opens when playback
   really ends. The server accepts the answer from the end of the question until that
   window closes, so both agree on when it ends and an early answer isn't lost;
4. take exactly one segment (short ones, ~0.2 s, are expected here), transcribe it,
   and answer `{type:"confirm_result", accepted, reason}`. The answer's text is
   discarded like any other wake audio.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from sasha.server.voice import MIC_SAMPLE_RATE
from sasha.server.wake import confirm_reason

if TYPE_CHECKING:
    from sasha.server.wake_session import ListenerConversation

CONFIRM_SPEECH_WAIT_S = 30.0  # longest we wait for the question to be spoken and played
BYTES_PER_SAMPLE = 2


class PlaybackClock:
    """Estimates when the listener finishes *playing* this socket's speech."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.rate = 0
        self.started = 0.0
        self.sent_s = 0.0  # audio duration sent since tts_start
        self.ends_at = 0.0

    def tts_start(self, sample_rate: int) -> None:
        self.rate = max(1, int(sample_rate))
        self.started = self.clock()
        self.sent_s = 0.0
        self.ends_at = self.started

    def frame(self, size: int) -> None:
        if not self.rate:
            return
        duration = size / (BYTES_PER_SAMPLE * self.rate)
        self.sent_s += duration
        # Continuous playback from tts_start, unless frames came slower than real time.
        self.ends_at = max(self.started + self.sent_s, self.clock() + duration)

    def remaining(self, tail_s: float) -> float:
        return max(0.0, self.ends_at + tail_s - self.clock())


class ConfirmFlow:
    def __init__(self, conv: ListenerConversation) -> None:
        self.conv = conv
        self.waiter: asyncio.Future[bytes] | None = None

    # --- from the LLM worker thread ---------------------------------------------------
    def blocking(self, action: str) -> bool:
        conv = self.conv
        future = asyncio.run_coroutine_threadsafe(self.ask(action), conv.loop)
        try:
            limit = 2 * CONFIRM_SPEECH_WAIT_S + conv.config.confirm_window_s + 5
            return bool(future.result(timeout=limit))
        except Exception:
            future.cancel()
            return False

    # --- on the event loop --------------------------------------------------------------
    def offer(self, audio: bytes) -> bool:
        """A finished segment: take it if a question is waiting for its answer."""
        if self.waiter is not None and not self.waiter.done():
            self.waiter.set_result(audio)
            return True
        return False

    def cancel(self) -> None:
        self.offer(b"")  # a pending question counts as "no"

    async def ask(self, action: str) -> bool:
        conv = self.conv
        if conv.stop.is_set() or conv.stt is None:
            return False
        window = conv.config.confirm_window_s
        conv.emit({"type": "confirm", "action": action, "timeout_s": window})
        await self._speak(f"Should I {action}?")
        # The listener's window opens when the question has finished *playing* (plus
        # the tail) and lasts `window`. Accept the answer from now until that window
        # closes, so an answer that is a little early isn't lost.
        self.waiter = conv.loop.create_future()
        deadline = conv.playback.remaining(conv.config.speaking_tail_s) + window
        try:
            audio = await asyncio.wait_for(self.waiter, deadline)
        except asyncio.TimeoutError:
            conv.record_tool("confirm_timeout")
            return self._result("timeout")
        finally:
            self.waiter = None
        if not audio:  # cancelled or disconnected
            return self._result("no")
        try:
            async with conv.voice.stt_lock:
                answer = await asyncio.to_thread(conv.stt.transcribe, audio, MIC_SAMPLE_RATE)
        except Exception:
            return self._result("unclear")
        cfg = conv.config
        reason = confirm_reason(answer.text, cfg.yes_words, cfg.no_words, cfg.fillers)
        del answer, audio  # the answer's text is not kept, like any other wake audio
        return self._result(reason)

    async def _speak(self, question: str) -> None:
        conv = self.conv
        current = conv.speaker
        if current is None:
            return  # text-only listener: no speech, the window opens at once
        await current.finish()  # what the turn already said, ending with its tts_end
        asker = conv.make_turn_speaker()
        if asker is not None:
            asker.say(question)
            try:
                await asyncio.wait_for(asker.finish(), CONFIRM_SPEECH_WAIT_S)
            except asyncio.TimeoutError:
                asker.cancel()
        conv.speaker = conv.make_turn_speaker()  # the rest of the turn speaks afresh

    def _result(self, reason: str) -> bool:
        accepted = reason == "yes"
        self.conv.emit({"type": "confirm_result", "accepted": accepted, "reason": reason})
        return accepted
