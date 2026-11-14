"""Text-to-speech stage (Phase 1/2, Indic Parler-TTS). Voice comes from assistant.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SpeechAudio:
    pcm: bytes
    sample_rate: int


class TextToSpeech(Protocol):
    def model_version(self) -> str: ...

    def synthesize(self, text: str, language: str) -> SpeechAudio: ...
