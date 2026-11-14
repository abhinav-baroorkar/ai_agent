"""English text-to-speech with Kokoro-82M (ONNX, CPU), streamed sentence by sentence.

Needs the optional `voice` extra. Licences: kokoro-onnx is MIT and the Kokoro-82M
weights are Apache-2.0, but kokoro-onnx's G2P pulls in `phonemizer` and
`espeakng-loader` (espeak-ng), which are GPL-3.0. Those are imported lazily, only
here, and only through `KokoroTTS._phonemize`, so the G2P can be replaced later
without touching the rest of the stage.

Privacy: reply text never goes into logs or exception messages from this module.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sasha.config import TTSConfig, Voice
from sasha.pipeline.model_cache import (
    ModelNotDownloadedError,
    ensure_project_hf_env,
    hf_cache_dir,
    require_inside,
)
from sasha.pipeline.tts import SpeechAudio, split_sentences

log = logging.getLogger(__name__)
# phonemizer logs "words count mismatch" on every call; it is harmless for Kokoro.
logging.getLogger("phonemizer").setLevel(logging.ERROR)

SAMPLE_RATE = 24000
STYLE_DIM = 256


class SpeechError(RuntimeError):
    """TTS failed. The message never contains the text being spoken."""


def kokoro_lang(voice_id: str) -> str:
    """Kokoro voice ids start with a language letter: a = US English, b = UK English."""
    return "en-gb" if voice_id.startswith("b") else "en-us"


def float_to_pcm16(audio: NDArray[np.float32]) -> bytes:
    clipped = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    return bytes((clipped * 32767.0).astype("<i2").tobytes())


def cap_text(text: str, max_chars: int) -> str:
    """Cut `text` to at most `max_chars`, at a sentence end if there is one in range,
    else at the last space."""
    if len(text) <= max_chars:
        return text
    kept = ""
    for sentence in split_sentences(text):
        candidate = f"{kept} {sentence}".strip()
        if len(candidate) > max_chars:
            break
        kept = candidate
    if not kept:
        head = text[:max_chars]
        kept = head.rsplit(" ", 1)[0] if " " in head else head
    return kept


def model_files(
    config: TTSConfig, voice_id: str, root: Path, download: bool = False
) -> tuple[Path, Path]:
    """Paths of the ONNX model and the voice's style file in the project HF cache."""
    ensure_project_hf_env(root)  # before huggingface_hub is imported
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    cache = hf_cache_dir(root)
    paths = []
    for filename in (config.model_file, f"{config.voices_dir}/{voice_id}.bin"):
        try:
            path = hf_hub_download(
                config.model_repo,
                filename,
                revision=config.revision,
                cache_dir=str(cache),
                local_files_only=not download,
            )
        except LocalEntryNotFoundError as e:
            raise ModelNotDownloadedError(f"Kokoro '{filename}'", cache) from e
        paths.append(Path(path))
    return paths[0], paths[1]


def _valid_style(style: Any) -> bool:
    return (
        isinstance(style, np.ndarray)
        and style.dtype == np.float32
        and style.ndim == 3
        and style.shape[0] > 0
        and style.shape[1:] == (1, STYLE_DIM)
    )


def _read_npz(path: Path, voice_id: str) -> bool:
    """True if `path` is a pickle-free .npz holding exactly a valid style for the voice."""
    try:
        with np.load(path, allow_pickle=False) as data:
            return list(data.files) == [voice_id] and _valid_style(data[voice_id])
    except (OSError, ValueError, KeyError):
        return False


def voices_npz(voice_bin: Path, voice_id: str, revision: str, root: Path) -> Path:
    """kokoro-onnx loads voices from one .npz; the official repo ships one raw .bin per
    voice. Pack the voice into a small .npz next to the HF cache (inside the project),
    written atomically and checked (shape, dtype, no pickles) before every use."""
    out = hf_cache_dir(root).parent / "derived" / f"kokoro-{revision[:12]}-{voice_id}.npz"
    require_inside(out, root, "Kokoro voice file")
    if _read_npz(out, voice_id):
        return out
    raw = np.fromfile(voice_bin, dtype="<f4")
    if raw.size == 0 or raw.size % STYLE_DIM:
        raise SpeechError(f"Voice file for {voice_id!r} has an unexpected size ({raw.size})")
    style = raw.astype(np.float32).reshape(-1, 1, STYLE_DIM)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out.parent, prefix=f".{out.stem}-", suffix=".npz")
    try:
        with os.fdopen(fd, "wb") as f:
            arrays: dict[str, Any] = {voice_id: style}
            np.savez(f, **arrays)
        if not _read_npz(Path(tmp_name), voice_id):
            raise SpeechError(f"Could not write a valid voice file for {voice_id!r}")
        os.replace(tmp_name, out)  # atomic on the same filesystem
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
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
        self._g2p_lock = threading.Lock()  # espeak-ng is not thread-safe; don't rely on
        # kokoro-onnx's own lock
        model_path, voice_bin = model_files(config, self.voice_id, root)
        voices_path = voices_npz(voice_bin, self.voice_id, config.revision, root)

        import onnxruntime as ort
        from kokoro_onnx import Kokoro  # lazy: pulls in GPL-3.0 phonemizer/espeak-ng

        options = ort.SessionOptions()
        options.intra_op_num_threads = config.threads
        session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        self._kokoro: Any = Kokoro.from_session(session, str(voices_path))
        if self.voice_id not in self._kokoro.voices:
            raise ValueError(f"Unknown Kokoro voice {self.voice_id!r}")

    @property
    def languages(self) -> tuple[str, ...]:
        """Languages this stage can speak (ISO 639-1)."""
        return ("en",)

    def model_version(self) -> str:
        return f"kokoro-82m-v1.0-onnx@{self.config.revision}:{self.voice_id}"

    def _phonemize(self, text: str) -> str:
        """The G2P seam: text -> Kokoro phoneme string. Swap this to drop espeak-ng."""
        with self._g2p_lock:
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
        capped = cap_text(text, self.config.max_chars)
        if len(capped) < len(text):
            log.warning("TTS input cut from %d to %d characters", len(text), len(capped))
        for sentence in split_sentences(capped):
            try:
                clip = self._speak(sentence)
            except ValueError as e:  # e.g. only symbols outside the vocabulary
                log.warning(
                    "TTS skipped a sentence (%d chars): %s", len(sentence), type(e).__name__
                )
                continue
            except Exception as e:
                # kokoro-onnx messages can quote the text: keep it out of logs and tracebacks.
                raise SpeechError(f"TTS failed ({type(e).__name__})") from None
            if clip is not None:
                yield clip

    def synthesize(self, text: str, language: str) -> SpeechAudio:
        pcm = b"".join(clip.pcm for clip in self.stream(text, language))
        return SpeechAudio(pcm=pcm, sample_rate=SAMPLE_RATE)
