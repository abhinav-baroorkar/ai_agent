"""A ring on one socket, and the listener's stop window (design 3, D1).

`RingPlayer`: the announcement (spoken by the TTS stage) or one tone, sent as an
ordinary `tts_start` / PCM frames / `tts_end`, so the brain-wide speaking tracker
pauses the listener's microphone for each. Never in the middle of a turn's speech:
it waits for the turn to end (at most `ring_wait_s`, else it skips that one; the
tones go on). One ring at a time per socket.

`StopWindow` (listener only), after the ring's playback has ended and
`resume_capture` has gone out, and only if capture is allowed anyway: send
`stop_window`, take one segment, and accept it only if it is at most 2 words, starts
with exactly a stop word, and passes the name-alone confidence threshold. Like a
confirm answer, its text is never kept, logged or sent to the model. A segment that
doesn't count goes on to the normal wake check (so "<name>, stop" still works).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import threading
from typing import TYPE_CHECKING, Any

from sasha.pipeline.stt import Transcript
from sasha.server.speech import Speaker
from sasha.server.timers.hub import TONE, TONE_RATE
from sasha.server.timers.service import TimerError
from sasha.server.voice import pcm_frames

if TYPE_CHECKING:
    from sasha.server.timers.hub import Hub
    from sasha.server.wake_session import ListenerConversation
    from sasha.server.ws import Conversation

log = logging.getLogger(__name__)
MAX_STOP_WORDS = 2
MIN_WINDOW_S = 1.0  # don't open a window shorter than this before the next tone
RESUME_WAIT_S = 5.0


class RingPlayer:
    def __init__(self, conv: Conversation, hub: Hub) -> None:
        self.conv = conv
        self.hub = hub
        self.lock: asyncio.Lock | None = None
        self.speaker: Speaker | None = None

    def ring_threadsafe(self, item_id: str, say: str, tone: bool) -> None:
        with contextlib.suppress(RuntimeError):  # the socket's loop is gone
            asyncio.run_coroutine_threadsafe(self.play(item_id, say, tone), self.conv.loop)

    async def play(self, item_id: str, say: str, tone: bool) -> None:
        conv = self.conv
        self.lock = self.lock or asyncio.Lock()
        async with self.lock:
            if not await self._turn_done():
                return  # a long turn is speaking: skip this one, the next tone follows
            conv.ring_started(item_id)
            if tone:
                conv.emit({"type": "tts_start", "sample_rate": TONE_RATE})
                for frame in pcm_frames(TONE, TONE_RATE):
                    conv.emit(frame)
                conv.emit({"type": "tts_end"})
            elif say:
                await self._say(say)
        await conv.ring_played(item_id)

    async def _turn_done(self) -> bool:
        wait = self.hub.config.ring_wait_s
        while self.conv.busy and wait > 0:
            await asyncio.sleep(0.05)
            wait -= 0.05
        return not self.conv.busy

    async def _say(self, text: str) -> None:
        conv, voice = self.conv, self.conv.voice
        if voice.tts is None or conv.language not in voice.languages:
            return  # no voice: the UI shows `say` from timer_ringing
        speaker = Speaker(
            voice.tts, conv.language, voice.split, voice.tts_lock, conv.emit, threading.Event()
        )
        self.speaker = speaker
        try:
            speaker.say(text)
            await speaker.finish()
        finally:
            self.speaker = None
            if speaker.started and not conv.busy:
                conv.state("idle")

    def close(self) -> None:
        if self.speaker is not None:
            self.speaker.cancel()


def stop_action(heard: Transcript, stop_words: list[str], min_confidence: float) -> str | None:
    """D1: "stop" / "dismiss" -> "dismiss", "snooze" -> "snooze", else None."""
    words = re.findall(r"[\w']+", heard.text.lower())
    if not words or len(words) > MAX_STOP_WORDS or words[0] not in stop_words:
        return None
    if heard.confidence is not None and heard.confidence < min_confidence:
        return None
    return "snooze" if words[0] == "snooze" else "dismiss"


class StopWindow:
    def __init__(self, conv: ListenerConversation, hub: Hub) -> None:
        self.conv = conv
        self.hub = hub
        self.item_id: str | None = None
        self.waiter: asyncio.Future[bytes] | None = None

    def _ringing_here(self, item_id: str) -> bool:
        item = self.hub.service.items.get(item_id)
        ringing = item is not None and item.state == "ringing"
        return ringing and self.hub.rings_on(item_id, self.conv)

    def _capture_allowed(self) -> bool:
        conv = self.conv
        return not (
            conv.paused
            or conv.busy
            or conv.audio is not None
            or conv.confirm.waiter is not None
            or conv.capture_paused
            or conv.voice.speaking.speaking()
        )

    async def open_after(self, item_id: str) -> None:
        """After the ring's playback (and resume_capture), offer one stop segment."""
        conv, cfg = self.conv, self.hub.config
        if self.waiter is not None or not self._ringing_here(item_id):
            return
        await asyncio.sleep(conv.playback.remaining(conv.config.speaking_tail_s))
        waited = 0.0
        while conv.capture_paused and waited < RESUME_WAIT_S:  # resume_capture not out yet
            await asyncio.sleep(0.02)
            waited += 0.02
        service = self.hub.service
        next_tone = service.next_tone_in(item_id)
        timeout = min(cfg.stop_window_s, next_tone - 0.5)
        if timeout < MIN_WINDOW_S or not self._ringing_here(item_id):
            return
        if not self._capture_allowed() or self.waiter is not None:
            return
        self.item_id, self.waiter = item_id, conv.loop.create_future()
        conv.emit({"type": "stop_window", "id": item_id, "timeout_s": round(timeout, 1)})
        try:
            await asyncio.wait_for(asyncio.shield(self.waiter), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            if not self.waiter.done():
                self.waiter.cancel()
            self.waiter = self.item_id = None

    def offer(self, audio: bytes) -> str | None:
        """A finished segment while the window is open: the item id it answers."""
        if self.waiter is None or self.waiter.done() or self.item_id is None:
            return None
        item_id = self.item_id
        self.waiter.set_result(audio)
        return item_id

    def close(self) -> None:
        """A new ring or tone on this socket, or a disconnect: the window ends."""
        if self.waiter is not None and not self.waiter.done():
            self.waiter.set_result(b"")

    def judge(self, item_id: str, heard: Transcript) -> bool:
        """Act on a stop segment. True if it counted (then nothing else sees it)."""
        cfg, wake = self.hub.config, self.conv.config
        action = stop_action(heard, [w.lower() for w in cfg.stop_words], wake.min_confidence)
        accepted = False
        if action is not None and self._ringing_here(item_id):
            try:
                if action == "snooze":
                    self.hub.service.snooze(item_id, bare=True)
                else:
                    self.hub.service.dismiss(item_id)
                accepted = True
            except TimerError as exc:
                self.conv.record_tool(f"stop_{exc.reason}")
        frame: dict[str, Any] = {"type": "stop_result", "id": item_id, "accepted": accepted,
                                 "action": action if accepted else None}  # fmt: skip
        self.conv.emit(frame)
        self.conv.record_tool("stop_accepted" if accepted else "stop_ignored")
        return accepted
