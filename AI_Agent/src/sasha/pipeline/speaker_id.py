"""Speaker-ID stage (Phase 3, SpeechBrain ECAPA). Used for permissions, not security."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sasha.pipeline.audio import AudioChunk


@dataclass(frozen=True)
class SpeakerMatch:
    speaker: str  # enrolled id, or "guest"
    score: float


class SpeakerIdentifier(Protocol):
    def identify(self, audio: AudioChunk, sample_rate: int) -> SpeakerMatch: ...
