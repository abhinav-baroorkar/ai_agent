"""Text-to-speech stage. Voice and speed come from assistant.yaml, never from code."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SpeechAudio:
    pcm: bytes  # signed 16-bit little-endian mono
    sample_rate: int


class TextToSpeech(Protocol):
    @property
    def languages(self) -> tuple[str, ...]: ...

    def model_version(self) -> str: ...

    def synthesize(self, text: str, language: str) -> SpeechAudio: ...

    # stream(): one SpeechAudio per sentence, in order, so playback can start early.
    def stream(self, text: str, language: str) -> Iterator[SpeechAudio]: ...


_SENTENCE_END = re.compile(r"(?<=[.!?…।])\s+|\n+")  # . ! ? ellipsis, Devanagari danda


def split_sentences(text: str, min_chars: int = 12) -> list[str]:
    """Split text at sentence ends. Pieces shorter than `min_chars` join the next one,
    so "OK." or "1." don't become their own choppy clip."""
    pieces = [p.strip() for p in _SENTENCE_END.split(text) if p and p.strip()]
    out: list[str] = []
    carry = ""
    for piece in pieces:
        carry = f"{carry} {piece}".strip()
        if len(carry) >= min_chars:
            out.append(carry)
            carry = ""
    if carry:
        if out:
            out[-1] = f"{out[-1]} {carry}"
        else:
            out.append(carry)
    return out
