"""English text-to-speech with Kokoro-82M (ONNX, CPU), streamed sentence by sentence.

Needs the optional `voice` extra. Licences: kokoro-onnx is MIT and the Kokoro-82M
weights are Apache-2.0, but kokoro-onnx's G2P pulls in `phonemizer` and
`espeakng-loader` (espeak-ng), which are GPL-3.0. Those are imported lazily, only
here, and only through `KokoroTTS._phonemize`, so the G2P can be replaced later
without touching the rest of the stage.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sasha.config import TTSConfig, Voice
from sasha.pipeline.model_cache import ModelNotDownloadedError, hf_cache_dir
from sasha.pipeline.tts import SpeechAudio, split_sentences

log = logging.getLogger(__name__)
# phonemizer logs "words count mismatch" on every call; it is harmless for Kokoro.
logging.getLogger("phonemizer").setLevel(logging.ERROR)

SAMPLE_RATE = 24000


def kokoro_lang(voice_id: str) -> str:
    """Kokoro voice ids start with a language letter: a = US English, b = UK English."""
    return "en-gb" if voice_id.startswith("b") else "en-us"


def float_to_pcm16(audio: NDArray[np.float32]) -> bytes:
    clipped = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    return bytes((clipped * 32767.0).astype("<i2").tobytes())


STYLE_DIM = 256


def model_files(
    config: TTSConfig, voice_id: str, root: Path, download: bool = False
) -> tuple[Path, Path]:
    """Paths of the ONNX model and the voice's style file in the project HF cache."""
    from huggingface_hub import hf_hub_download

    cache = hf_cache_dir(root)
    paths = []
    for filename in (config.model_file, f"{config.voices_dir}/{voice_id}.bin"):
        try:
            paths.append(
                Path(
                    hf_hub_download(
                        config.model_repo,
                        filename,
                        revision=config.revision,
                        cache_dir=str(cache),
                        local_files_only=not download,
                    )
                )
            )
        except Exception as e:
            if download:
                raise
            raise ModelNotDownloadedError(f"Kokoro '{filename}'", cache) from e
    return paths[0], paths[1]


def voices_npz(voice_bin: Path, voice_id: str, revision: str, root: Path) -> Path:
    """kokoro-onnx loads voices from one .npz; the official repo ships one raw .bin per
    voice. Pack the voice into a small .npz next to the HF cache (inside the project)."""
    out = hf_cache_dir(root).parent / "derived" / f"kokoro-{revision[:12]}-{voice_id}.npz"
    if not out.is_file():
        style = np.fromfile(voice_bin, dtype=np.float32).reshape(-1, 1, STYLE_DIM)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.npz")
        arrays: dict[str, Any] = {voice_id: style}
        np.savez(tmp, **arrays)
        tmp.replace(out)
    return out


class KokoroTTS:
    """`TextToSpeech` on kokoro-onnx. English only for now; Marathi comes in Phase 2."""

    def __init__(self, config: TTSConfig, voice: Voice, root: Path) -> None:
        if config.device != "cpu":
            raise ValueError(f"Kokoro TTS runs on CPU only for now (got device={config.device!r})")
        self.config = config
        self.voice_id = voice.en_voice
        self.speed = voice.speed
        self.lang = kokoro_lang(self.voice_id)
        model_path, voice_bin = model_files(config, self.voice_id, root)
        voices_path = voices_npz(voice_bin, self.voice_id, config.revision, root)

        import onnxruntime as ort
        from kokoro_onnx import Kokoro  # lazy: pulls in GPL-3.0 phonemizer/espeak-ng

        options = ort.SessionOptions()
        options.intra_op_num_threads = config.threads
        session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        self._kokoro: Any = Kokoro.from_session(session, str(voices_path))
        if self.voice_id not in self._kokoro.voices:
            known = ", ".join(sorted(self._kokoro.voices))
            raise ValueError(f"Unknown Kokoro voice {self.voice_id!r}. Known: {known}")

    @property
    def languages(self) -> tuple[str, ...]:
        """Languages this stage can speak (ISO 639-1)."""
        return ("en",)

    def model_version(self) -> str:
        return f"kokoro-82m-v1.0-onnx@{self.config.revision[:8]}:{self.voice_id}"

    def _phonemize(self, text: str) -> str:
        """The G2P seam: text -> Kokoro phoneme string. Swap this to drop espeak-ng."""
        return str(self._kokoro.tokenizer.phonemize(text, self.lang))

    def _speak(self, sentence: str) -> SpeechAudio | None:
        phonemes = self._phonemize(sentence)
        if not phonemes:
            return None
        audio, rate = self._kokoro.create(
            phonemes, voice=self.voice_id, speed=self.speed, is_phonemes=True
        )
        return SpeechAudio(pcm=float_to_pcm16(audio), sample_rate=int(rate))

    def stream(self, text: str, language: str) -> Iterator[SpeechAudio]:
        if language not in self.languages:
            log.warning("Kokoro TTS speaks %s only; got language=%r", self.languages, language)
        for sentence in split_sentences(text):
            try:
                clip = self._speak(sentence)
            except ValueError as e:  # e.g. only symbols outside the vocabulary
                log.warning("TTS skipped a sentence: %s", e)
                continue
            if clip is not None:
                yield clip

    def synthesize(self, text: str, language: str) -> SpeechAudio:
        pcm = b"".join(clip.pcm for clip in self.stream(text, language))
        return SpeechAudio(pcm=pcm, sample_rate=SAMPLE_RATE)
