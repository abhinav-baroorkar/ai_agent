"""Speak an answer while it is still streaming (sockets opened with `speak=1`).

Deltas are buffered; whenever the sentence splitter returns more than one piece, all
but the last are queued for TTS and the last stays in the buffer. A buffer that grows
past `MAX_BUFFER_CHARS` with no sentence end is cut at a comma or space, so a long
answer without full stops still starts speaking (and re-splitting stays cheap).
`finish()` flushes the rest.

One worker task per answer synthesises the queue in order, in a thread, holding the
stage's lock so two sockets never run the TTS model at once. `stop` (the
conversation's cancel event) drops queued pieces and ends speech in flight at its
next chunk. A cancelled worker task can't stop its thread mid-call: that thread may
still compute up to one more chunk after the lock is released, so it can briefly
overlap another socket's speech. A chunk that takes longer than `CHUNK_TIMEOUT_S`
counts as a hung stage: "Speech failed" is sent and the rest of that answer's speech
is dropped, so one stuck stage can't block every socket.
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
MAX_BUFFER_CHARS = 200
CHUNK_TIMEOUT_S = 30.0
_SOFT_BREAKS = (", ", "; ", ": ", " ")


def cut_long(text: str, limit: int = MAX_BUFFER_CHARS) -> tuple[str, str]:
    """Split `text` into (head, tail) at the last soft break before `limit`."""
    window = text[:limit]
    for mark in _SOFT_BREAKS:
        at = window.rfind(mark)
        if at > 0:
            return text[: at + len(mark)].strip(), text[at + len(mark) :]
    return text[:limit], text[limit:]  # one very long word: hard cut


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
        self.failed = False
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.task = asyncio.create_task(self._work())

    def feed(self, text: str) -> None:
        """Add streamed answer text. Call on the event loop."""
        if self.stop.is_set() or self.failed:
            return
        self.buffer += text
        pieces = self.split(self.buffer)
        if len(pieces) > 1:
            for piece in pieces[:-1]:
                self.queue.put_nowait(piece)
            self.buffer = pieces[-1]
        while len(self.buffer) > MAX_BUFFER_CHARS:
            head, self.buffer = cut_long(self.buffer)
            if head:
                self.queue.put_nowait(head)

    async def finish(self) -> None:
        """Speak what is left in the buffer and wait until speech is done (or stopped)."""
        rest, self.buffer = self.buffer.strip(), ""
        if rest and not self.stop.is_set():
            self.queue.put_nowait(rest)
        self.queue.put_nowait(None)
        await asyncio.wait({self.task})  # unlike `await task`, a cancel of *our* caller propagates

    async def abort(self) -> None:
        """Drop everything queued; speech in flight ends at its next chunk."""
        self.buffer = ""
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(None)
        await asyncio.wait({self.task})

    def cancel(self) -> None:
        """Hard stop (socket closed): cancel the worker without waiting."""
        self.stop.set()
        self.task.cancel()

    async def _work(self) -> None:
        try:
            while (piece := await self.queue.get()) is not None:
                if self.stop.is_set() or self.failed:
                    break
                async with self.lock:
                    await self._say(piece)
        except asyncio.TimeoutError:
            self._fail("TTS stage timed out")
        except Exception as exc:  # speech failing must not break the text answer
            self._fail(type(exc).__name__)
        finally:
            if self.started:
                self.emit({"type": "tts_end"})

    def _fail(self, why: str) -> None:
        log.error("Speech failed: %s", why)
        self.failed = True
        self.emit({"type": "error", "message": "Speech failed"})  # never the answer text

    async def _say(self, piece: str) -> None:
        chunks: Iterator[SpeechAudio] = await asyncio.wait_for(
            asyncio.to_thread(self.tts.stream, piece, self.language), CHUNK_TIMEOUT_S
        )
        try:
            while not self.stop.is_set():
                chunk = await asyncio.wait_for(
                    asyncio.to_thread(next, chunks, None), CHUNK_TIMEOUT_S
                )
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
                # Fails harmlessly ("generator already executing") if a timed-out
                # thread still runs it; the generator is then closed when collected.
                with contextlib.suppress(Exception):
                    close()
