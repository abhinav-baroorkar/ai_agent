"""English (and language-ID) speech-to-text with faster-whisper (CTranslate2).

Needs the optional `voice` extra. The import is lazy so the base package and CI
load without it. Marathi transcription quality from Whisper `small` is poor; it is
used here for language ID, and Phase 2 routes Marathi to IndicConformer.
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sasha.config import STTConfig
from sasha.pipeline.audio import AudioChunk
from sasha.pipeline.model_cache import (
    ModelNotDownloadedError,
    ensure_project_hf_env,
    hf_cache_dir,
)
from sasha.pipeline.stt import Transcript

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
_CUDA_LIBS = ("cublas/lib/libcublasLt.so.12", "cublas/lib/libcublas.so.12")
_CUDNN_GLOB = "cudnn/lib/libcudnn*.so.9"


def preload_pip_cuda_libs() -> bool:
    """CTranslate2 dlopen()s cuBLAS 12 and cuDNN 9 by soname. If they came from the
    `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels (the `voice-gpu` extra), load them
    first so no LD_LIBRARY_PATH is needed. Returns True if cuBLAS was found."""
    for site in map(Path, sys.path):
        base = site / "nvidia"
        if not (base / _CUDA_LIBS[1]).is_file():
            continue
        try:
            for lib in [
                *_CUDA_LIBS,
                *sorted(str(p.relative_to(base)) for p in base.glob(_CUDNN_GLOB)),
            ]:
                ctypes.CDLL(str(base / lib), mode=ctypes.RTLD_GLOBAL)
        except OSError as e:
            log.warning("Could not preload CUDA libraries from %s: %s", base, e)
            return False
        return True
    return False


def pcm16_to_float(audio: AudioChunk) -> NDArray[np.float32]:
    """s16le bytes -> float32 in [-1, 1]."""
    if len(audio) % 2:
        raise ValueError("PCM16 audio must have an even number of bytes")
    return np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0


class FasterWhisperSTT:
    """`SpeechToText` on faster-whisper. Language is chosen among `languages` only."""

    def __init__(
        self,
        config: STTConfig,
        root: Path,
        languages: list[str] | None = None,
    ) -> None:
        ensure_project_hf_env(root)  # before faster_whisper imports huggingface_hub
        self.config = config
        self.languages = languages or ["en"]
        self.cache = hf_cache_dir(root)
        self.device = "cpu"
        self.compute_type = "int8"
        self._model: Any = self._load()

    def model_version(self) -> str:
        cfg = self.config
        return f"faster-whisper-{cfg.model}@{cfg.revision}:{self.device}:{self.compute_type}"

    def _load(self) -> Any:
        from faster_whisper import WhisperModel

        want = self.config.device
        if want in ("auto", "cuda"):
            preload_pip_cuda_libs()
            try:
                model = self._make(WhisperModel, "cuda")
                self._warm_up(model)  # missing cuBLAS/cuDNN only fails on first inference
                return model
            except ModelNotDownloadedError:
                raise
            except Exception as e:
                if want == "cuda":
                    raise RuntimeError(f"STT on CUDA failed: {e}") from e
                log.warning("STT: CUDA unavailable (%s); falling back to CPU int8", e)
        return self._make(WhisperModel, "cpu")

    def _make(self, cls: Any, device: str) -> Any:
        compute = self.config.compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        from huggingface_hub.errors import LocalEntryNotFoundError

        try:
            model = cls(
                self.config.model,
                device=device,
                compute_type=compute,
                cpu_threads=self.config.cpu_threads,
                download_root=str(self.cache),
                local_files_only=True,
                revision=self.config.revision,
            )
        except LocalEntryNotFoundError as e:
            what = f"Whisper '{self.config.model}'@{self.config.revision[:8]}"
            raise ModelNotDownloadedError(what, self.cache) from e
        self.device, self.compute_type = device, compute
        return model

    @staticmethod
    def _warm_up(model: Any) -> None:
        segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en")
        list(segments)

    def transcribe(self, audio: AudioChunk, sample_rate: int) -> Transcript:
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"STT expects {SAMPLE_RATE} Hz audio, got {sample_rate}")
        samples = pcm16_to_float(audio)
        seconds = samples.size / SAMPLE_RATE
        if seconds > self.config.max_seconds:
            raise ValueError(
                f"Utterance is {seconds:.0f} s; the limit is {self.config.max_seconds} s"
            )
        if samples.size < SAMPLE_RATE // 10:  # under 100 ms: nothing worth decoding
            return Transcript(text="", language=self.languages[0], confidence=0.0)

        forced = self.languages[0] if len(self.languages) == 1 else None
        text, info, logprob = self._run(samples, forced)
        language = info.language
        if language not in self.languages:
            # Whisper picked a language we don't support (e.g. Hindi for Marathi):
            # re-decode with the most likely supported one.
            probs = dict(info.all_language_probs or [])
            language = max(self.languages, key=lambda lang: probs.get(lang, 0.0))
            text, info, logprob = self._run(samples, language)
        confidence = round(min(1.0, math.exp(logprob)), 3) if text else 0.0
        return Transcript(text=text, language=language, confidence=confidence)

    def _run(self, samples: NDArray[np.float32], language: str | None) -> tuple[str, Any, float]:
        segments, info = self._model.transcribe(
            samples,
            language=language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            condition_on_previous_text=False,
        )
        parts, weights, total = [], 0.0, 0.0
        for seg in segments:
            parts.append(seg.text.strip())
            n = max(1, len(seg.tokens))
            weights += n
            total += seg.avg_logprob * n
        text = " ".join(p for p in parts if p)
        return text, info, (total / weights if weights else -math.inf)
