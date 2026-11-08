"""Speech-to-text stage (Phase 1: faster-whisper; Phase 2: IndicConformer for Marathi)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sasha.pipeline.audio import AudioChunk


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str  # ISO 639-1, e.g. "en", "mr"
    confidence: float | None = None


class SpeechToText(Protocol):
    def model_version(self) -> str: ...

    def transcribe(self, audio: AudioChunk, sample_rate: int) -> Transcript: ...
