"""Speak an answer while it is still streaming (sockets opened with `speak=1`).

Deltas are buffered; whenever the sentence splitter returns more than one piece, all
but the last are queued for TTS and the last stays in the buffer. `finish()` flushes
the rest. One worker task per answer synthesises the queue in order, in a thread,
holding the stage's lock so two sockets never run the TTS model at once. `stop`
(the conversation's cancel event) drops queued pieces and ends speech in flight at
its next chunk.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable, Iterator
from typing import Any

from sasha.pipeline.tts import SpeechAudio, TextToSpeech
from sasha.server.voice import pcm_frames

log = logging.getLogger(__name__)
Frame = dict[str, Any] | bytes


class Speaker:
    def __init__(
        self,
        tts: TextToSpeech,
        language: str,
        split: Callable[[str], list[str]],
        lock: asyncio.Lock,
        emit: Callable[[Frame], None],
        stop: threading.Event,
    ) -> None:
        self.tts = tts
        self.language = language
        self.split = split
        self.lock = lock
        self.emit = emit
        self.stop = stop
        self.buffer = ""
        self.started = False
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.task = asyncio.create_task(self._work())

    def feed(self, text: str) -> None:
        """Add streamed answer text. Call on the event loop."""
        if self.stop.is_set():
            return
        self.buffer += text
        pieces = self.split(self.buffer)
        if len(pieces) > 1:
            for piece in pieces[:-1]:
                self.queue.put_nowait(piece)
            self.buffer = pieces[-1]

    async def finish(self) -> None:
        """Speak what is left in the buffer and wait until speech is done (or stopped)."""
        rest, self.buffer = self.buffer.strip(), ""
        if rest and not self.stop.is_set():
            self.queue.put_nowait(rest)
        self.queue.put_nowait(None)
        with contextlib.suppress(asyncio.CancelledError):
            await self.task

    async def abort(self) -> None:
        """Drop everything queued; speech in flight ends at its next chunk."""
        self.buffer = ""
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(None)
        with contextlib.suppress(asyncio.CancelledError):
            await self.task

    async def _work(self) -> None:
        try:
            while (piece := await self.queue.get()) is not None:
                if self.stop.is_set():
                    break
                async with self.lock:
                    await self._say(piece)
        except Exception as exc:  # speech failing must not break the text answer
            log.error("Speech failed: %s", type(exc).__name__)
            self.emit({"type": "error", "message": "Speech failed"})
        finally:
            if self.started:
                self.emit({"type": "tts_end"})

    async def _say(self, piece: str) -> None:
        chunks: Iterator[SpeechAudio] = await asyncio.to_thread(
            self.tts.stream, piece, self.language
        )
        try:
            while not self.stop.is_set():
                chunk = await asyncio.to_thread(next, chunks, None)
                if chunk is None or self.stop.is_set():
                    break
                if not self.started:
                    self.started = True
                    self.emit({"type": "state", "state": "speaking"})
                    self.emit({"type": "tts_start", "sample_rate": chunk.sample_rate})
                for frame in pcm_frames(chunk.pcm, chunk.sample_rate):
                    self.emit(frame)
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(close)
