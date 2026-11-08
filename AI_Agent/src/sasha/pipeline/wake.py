"""Wake-word stage (Phase 1, openWakeWord). The phrase comes from assistant.yaml."""

from __future__ import annotations

from typing import Protocol

from sasha.pipeline.audio import AudioChunk


class WakeWordDetector(Protocol):
    def detect(self, chunk: AudioChunk) -> float:
        """Return a score in [0, 1] for the configured wake phrase."""
        ...

    def reset(self) -> None: ...
