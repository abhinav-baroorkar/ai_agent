"""Audio stage: microphone capture, speaker playback and voice-activity detection (Phase 1)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

AudioChunk = bytes
"""16-bit PCM mono at `sample_rate` Hz."""


class AudioSource(Protocol):
    sample_rate: int

    def frames(self) -> Iterator[AudioChunk]: ...


class AudioSink(Protocol):
    def play(self, pcm: AudioChunk, sample_rate: int) -> None: ...


class VoiceActivityDetector(Protocol):
    def is_speech(self, chunk: AudioChunk) -> bool: ...
