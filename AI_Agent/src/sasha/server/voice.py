"""Voice stages for the server, and helpers to frame audio for the WebSocket.

`load_voice_stages` is the one seam the voice branch fills in: it builds the real
STT/TTS from the profile. Until then it returns `None` for both, so `/api/health`
reports `voice: {stt: false, tts: false}` and `audio_start` gets an `error` frame.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import sasha.pipeline.tts as tts_module
from sasha.config import Settings
from sasha.pipeline.stt import SpeechToText
from sasha.pipeline.tts import TextToSpeech

MIC_SAMPLE_RATE = 16000  # contract: 16 kHz mono s16le from the client
BYTES_PER_SAMPLE = 2
FRAME_MS = 20
MAX_RECORDING_S = 60  # cap one utterance so a client can't fill memory
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def basic_split_sentences(text: str) -> list[str]:
    """Fallback splitter, used until `pipeline.tts.split_sentences` exists."""
    return [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]


def sentence_splitter() -> Callable[[str], list[str]]:
    """The TTS stage's `split_sentences` (voice branch) if present, else the fallback."""
    found = getattr(tts_module, "split_sentences", None)
    return found if callable(found) else basic_split_sentences


def tts_languages(tts: TextToSpeech | None) -> list[str]:
    """Language codes the TTS stage can speak (its `languages` property); [] without one."""
    if tts is None:
        return []
    return [str(code) for code in getattr(tts, "languages", None) or []]


@dataclass
class VoiceStages:
    """The voice stages one server shares across sockets, each with its own lock."""

    stt: SpeechToText | None = None
    tts: TextToSpeech | None = None
    split: Callable[[str], list[str]] = field(default_factory=sentence_splitter)
    stt_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tts_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def languages(self) -> list[str]:
        return tts_languages(self.tts)


def load_voice_stages(
    settings: Settings,
) -> tuple[SpeechToText | None, TextToSpeech | None, list[str]]:
    """Return (stt, tts, warnings). v0: no voice stages configured."""
    return None, None, []


def max_recording_bytes(sample_rate: int = MIC_SAMPLE_RATE) -> int:
    return sample_rate * BYTES_PER_SAMPLE * MAX_RECORDING_S


def pcm_frames(pcm: bytes, sample_rate: int, frame_ms: int = FRAME_MS) -> Iterator[bytes]:
    """Split s16le mono PCM into ~20 ms frames (whole samples only)."""
    size = max(BYTES_PER_SAMPLE, sample_rate * BYTES_PER_SAMPLE * frame_ms // 1000)
    size -= size % BYTES_PER_SAMPLE
    for start in range(0, len(pcm), size):
        yield pcm[start : start + size]
