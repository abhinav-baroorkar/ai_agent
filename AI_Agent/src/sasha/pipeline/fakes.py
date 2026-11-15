"""Model-free stand-ins for the speech stages, for tests and for running without GPUs."""

from __future__ import annotations

from collections.abc import Iterator

from sasha.pipeline.audio import AudioChunk
from sasha.pipeline.stt import Transcript
from sasha.pipeline.tts import SpeechAudio, split_sentences


class FakeSTT:
    """Returns a fixed transcript; records every call."""

    def __init__(self, text: str = "turn on the bedroom light", language: str = "en") -> None:
        self.text = text
        self.language = language
        self.calls: list[tuple[int, int]] = []  # (byte length, sample rate)

    def model_version(self) -> str:
        return "fake-stt"

    def transcribe(
        self, audio: AudioChunk, sample_rate: int, *, hotwords: str | None = None
    ) -> Transcript:
        self.calls.append((len(audio), sample_rate))
        if not audio:
            return Transcript(text="", language=self.language, confidence=0.0)
        return Transcript(text=self.text, language=self.language, confidence=1.0)


class FakeTTS:
    """Silence whose length grows with the text: `ms_per_char` per character."""

    def __init__(
        self,
        sample_rate: int = 24000,
        ms_per_char: int = 10,
        languages: tuple[str, ...] = ("en",),
    ) -> None:
        self.sample_rate = sample_rate
        self.ms_per_char = ms_per_char
        self._languages = languages
        self.spoken: list[str] = []

    @property
    def languages(self) -> tuple[str, ...]:
        return self._languages

    def model_version(self) -> str:
        return "fake-tts"

    def _clip(self, sentence: str) -> SpeechAudio:
        samples = self.sample_rate * self.ms_per_char * len(sentence) // 1000
        return SpeechAudio(pcm=b"\x00\x00" * samples, sample_rate=self.sample_rate)

    def stream(self, text: str, language: str) -> Iterator[SpeechAudio]:
        for sentence in split_sentences(text):
            self.spoken.append(sentence)
            yield self._clip(sentence)

    def synthesize(self, text: str, language: str) -> SpeechAudio:
        pcm = b"".join(c.pcm for c in self.stream(text, language))
        return SpeechAudio(pcm=pcm, sample_rate=self.sample_rate)
