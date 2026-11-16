"""Speech-to-text stage (Phase 1: faster-whisper; Phase 2: IndicConformer for Marathi)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sasha.pipeline.audio import AudioChunk


@dataclass(frozen=True)
class Word:
    """One decoded word: the decoder's own probability for it and its time in the audio."""

    text: str
    probability: float
    start: float = 0.0  # seconds from the segment's start
    end: float = 0.0


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str  # ISO 639-1, e.g. "en", "mr"
    confidence: float | None = None  # the segment's average (exp of the mean logprob)
    # Per word, only on wake segments (decoded with `hotwords`); empty otherwise. The wake
    # gate reads the phrase's own words from it (server/wake.py, phrase_probability).
    words: tuple[Word, ...] = ()


class SpeechToText(Protocol):
    def model_version(self) -> str: ...

    def transcribe(
        self, audio: AudioChunk, sample_rate: int, *, hotwords: str | None = None
    ) -> Transcript:
        """`hotwords`: words to favour while decoding (the wake word, for wake segments)."""
        ...
