"""Voice stages for the server, and helpers to frame audio for the WebSocket.

`load_voice_stages` is the one seam the voice branch fills in: it builds the real
STT/TTS from the profile. Until then it returns `None` for both, so `/api/health`
reports `voice: {stt: false, tts: false}` and `audio_start` gets an `error` frame.
"""

from __future__ import annotations

from collections.abc import Iterator

from sasha.config import Settings
from sasha.pipeline.stt import SpeechToText
from sasha.pipeline.tts import TextToSpeech

MIC_SAMPLE_RATE = 16000  # contract: 16 kHz mono s16le from the client
BYTES_PER_SAMPLE = 2
FRAME_MS = 20
MAX_RECORDING_S = 60  # cap one utterance so a client can't fill memory


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
