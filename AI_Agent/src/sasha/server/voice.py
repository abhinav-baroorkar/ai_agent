"""Voice stages for the server, and helpers to frame audio for the WebSocket.

`load_voice_stages` builds the real STT/TTS from the profile once at startup. A stage
that can't load (the `voice` extra isn't installed, weights aren't downloaded, a cache
path is outside the project) is `None` with a plain warning, so `/api/health` reports
it off and text chat keeps working.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TypeVar

from sasha.config import Settings
from sasha.pipeline.stt import SpeechToText
from sasha.pipeline.tts import TextToSpeech, split_sentences
from sasha.pipeline.voice import VoiceUnavailableError, build_stt, build_tts
from sasha.server.speaking import SpeakingTracker

log = logging.getLogger(__name__)

MIC_SAMPLE_RATE = 16000  # contract: 16 kHz mono s16le from the client
BYTES_PER_SAMPLE = 2
FRAME_MS = 20
MAX_RECORDING_S = 60  # cap one utterance so a client can't fill memory


def tts_languages(tts: TextToSpeech | None) -> list[str]:
    """Language codes the TTS stage can speak; [] without one."""
    return [] if tts is None else list(tts.languages)


@dataclass
class VoiceStages:
    """The voice stages one server shares across sockets, each with its own lock."""

    stt: SpeechToText | None = None
    tts: TextToSpeech | None = None
    split: Callable[[str], list[str]] = split_sentences
    stt_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tts_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Brain-wide: who is speaking, and when the last turn ended (wake word, D2).
    speaking: SpeakingTracker = field(default_factory=lambda: SpeakingTracker(0.3, 3.0))

    @property
    def languages(self) -> list[str]:
        return tts_languages(self.tts)


T = TypeVar("T")


def _try_build(
    name: str, build: Callable[[Settings], T], settings: Settings, warnings: list[str]
) -> T | None:
    """Build one stage; on any failure return None and add a plain warning."""
    try:
        return build(settings)
    except VoiceUnavailableError as e:
        why = str(e)
    except Exception as e:  # never let a voice stage stop the server
        why = f"failed to load ({type(e).__name__})"
    message = f"Voice {name} is off: {why}"
    log.warning(message)
    warnings.append(message)
    return None


def load_voice_stages(
    settings: Settings,
) -> tuple[SpeechToText | None, TextToSpeech | None, list[str]]:
    """Return (stt, tts, warnings). Never raises: a stage that can't load is None."""
    warnings: list[str] = []
    stt = _try_build("speech-to-text", build_stt, settings, warnings)
    tts = _try_build("text-to-speech", build_tts, settings, warnings)
    return stt, tts, warnings


def max_recording_bytes(sample_rate: int = MIC_SAMPLE_RATE) -> int:
    return sample_rate * BYTES_PER_SAMPLE * MAX_RECORDING_S


def pcm_frames(pcm: bytes, sample_rate: int, frame_ms: int = FRAME_MS) -> Iterator[bytes]:
    """Split s16le mono PCM into ~20 ms frames (whole samples only)."""
    size = max(BYTES_PER_SAMPLE, sample_rate * BYTES_PER_SAMPLE * frame_ms // 1000)
    size -= size % BYTES_PER_SAMPLE
    for start in range(0, len(pcm), size):
        yield pcm[start : start + size]
