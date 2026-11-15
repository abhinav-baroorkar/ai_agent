"""The cheap gate on the listener: decides which sounds are worth sending to the brain.

A chunk counts as speech when Silero's probability is >= 0.5 AND its loudness is at
least 10 dB over an adaptive noise floor (median level of the last 30 s of chunks the
VAD called non-speech).
A segment may only start after >= 300 ms of non-speech; it starts with a 1 s pre-roll
and ends after `end_silence_ms` (600 ms by default; the main knob for how fast a
command is taken) of non-speech. It is sent only if the speech in it spans
0.4-8 s; anything longer is dropped as soon as it passes 8 s.

Everything lives in RAM (bytes, a ring of chunks) and is dropped as soon as a segment
is returned or gated out. Nothing is written anywhere.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from sasha.listener.capture import CHUNK_BYTES, CHUNK_MS
from sasha.listener.vad import WINDOW, VoiceDetector

SPEECH_PROB = 0.5
LOUDER_DB = 10.0
FLOOR_WINDOW_S = 30.0
FLOOR_MIN_DB = -70.0  # floor before any non-speech has been heard
SILENCE_BEFORE_MS = 300
PRE_ROLL_MS = 1000
END_SILENCE_MS = 600
MIN_SPEECH_MS = 400
MAX_SPEECH_MS = 8000
_FLOOR_CHUNKS = int(FLOOR_WINDOW_S * 1000 / CHUNK_MS)
_PRE_ROLL_CHUNKS = PRE_ROLL_MS // CHUNK_MS


@dataclass(frozen=True)
class Segment:
    pcm: bytes  # 16 kHz mono s16le, pre-roll included
    speech_ms: int  # first to last speech chunk


@dataclass
class GateStats:
    """Counts only, never audio or text (sent to the brain as `listener_stats`)."""

    segments: int = 0
    too_short: int = 0
    too_long: int = 0

    @property
    def drops(self) -> int:
        return self.too_short + self.too_long


def chunk_db(samples: Any) -> float:
    """RMS level of int16 samples in dBFS (-inf safe)."""
    import numpy as np

    x = samples.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
    return 20.0 * math.log10(max(rms, 1e-6))


@dataclass
class _Segmenting:
    chunks: list[bytes] = field(default_factory=list)
    speech_ms: int = 0  # span: first speech chunk .. last speech chunk
    since_speech_ms: int = 0
    too_long: bool = False


class Gate:
    """Feed 20 ms chunks; get a `Segment` back when one is complete and passes."""

    def __init__(self, vad: VoiceDetector, end_silence_ms: int = END_SILENCE_MS) -> None:
        import numpy as np

        if end_silence_ms < CHUNK_MS:
            raise ValueError("end_silence_ms must be at least one chunk")
        self._np = np
        self.vad = vad
        self.end_silence_ms = end_silence_ms
        self.stats = GateStats()
        self._floor: deque[float] = deque(maxlen=_FLOOR_CHUNKS)
        self._floor_value = FLOOR_MIN_DB
        self._floor_age = 0
        self.reset()

    @property
    def noise_floor_db(self) -> float:
        return self._floor_value

    def reset(self) -> None:
        """Forget audio (the microphone was closed). The next segment again needs 300 ms
        of silence first, so speech is never picked up mid-sentence after a resume."""
        self.vad.reset()
        self._ring: deque[bytes] = deque(maxlen=_PRE_ROLL_CHUNKS)
        self._pending: Any = self._np.zeros(0, dtype=self._np.float32)
        self._prob = 0.0
        self._silence_ms = 0
        self._seg: _Segmenting | None = None

    def _speech_probability(self, samples: Any) -> float:
        np = self._np
        self._pending = np.concatenate([self._pending, samples.astype(np.float32) / 32768.0])
        while self._pending.size >= WINDOW:
            self._prob = self.vad.probability(self._pending[:WINDOW])
            self._pending = self._pending[WINDOW:]
        return self._prob

    def _update_floor(self, db: float) -> None:
        self._floor.append(db)
        self._floor_age += 1
        if self._floor_age >= 25 or len(self._floor) < 25:  # refresh twice a second
            self._floor_age = 0
            self._floor_value = float(self._np.median(self._np.fromiter(self._floor, float)))

    def feed(self, chunk: bytes) -> Segment | None:
        if len(chunk) != CHUNK_BYTES:
            raise ValueError(f"chunks must be {CHUNK_BYTES} bytes")
        samples = self._np.frombuffer(chunk, dtype="<i2")
        db = chunk_db(samples)
        prob = self._speech_probability(samples)
        voiced = prob >= SPEECH_PROB
        speech = voiced and db >= self._floor_value + LOUDER_DB
        if not voiced:  # only what the VAD calls non-speech feeds the noise floor, so
            self._update_floor(db)  # a long talk can't raise the floor to its own level
        if self._seg is None:
            self._idle(chunk, speech)
            return None
        return self._segmenting(self._seg, chunk, speech)

    def _idle(self, chunk: bytes, speech: bool) -> None:
        if speech and self._silence_ms >= SILENCE_BEFORE_MS:
            self._seg = _Segmenting(chunks=[*self._ring, chunk], speech_ms=CHUNK_MS)
            self._ring.clear()
            return
        self._silence_ms = 0 if speech else self._silence_ms + CHUNK_MS
        self._ring.append(chunk)

    def _segmenting(self, seg: _Segmenting, chunk: bytes, speech: bool) -> Segment | None:
        if not seg.too_long:
            seg.chunks.append(chunk)
        if speech:
            seg.speech_ms += seg.since_speech_ms + CHUNK_MS
            seg.since_speech_ms = 0
        else:
            seg.since_speech_ms += CHUNK_MS
        if seg.speech_ms > MAX_SPEECH_MS and not seg.too_long:
            seg.too_long, seg.chunks = True, []  # drop the audio now; wait for silence
        if seg.since_speech_ms < self.end_silence_ms:
            return None
        self._seg = None
        self._silence_ms = seg.since_speech_ms
        if seg.too_long:
            self.stats.too_long += 1
            return None
        if seg.speech_ms < MIN_SPEECH_MS:
            self.stats.too_short += 1
            return None
        self.stats.segments += 1
        return Segment(pcm=b"".join(seg.chunks), speech_ms=seg.speech_ms)
