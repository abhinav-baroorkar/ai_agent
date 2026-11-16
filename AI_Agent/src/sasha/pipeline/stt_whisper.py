"""English (and language-ID) speech-to-text with faster-whisper (CTranslate2).

Needs the optional `voice` extra. The import is lazy so the base package and CI
load without it. Marathi transcription quality from Whisper `small` is poor; it is
used here for language ID, and Phase 2 routes Marathi to IndicConformer.
"""

from __future__ import annotations

import ctypes
import logging
import math
import re
import sys
import threading
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
from sasha.pipeline.stt import Transcript, Word

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


# Case-sensitive on purpose: these are the CTranslate2/CUDA runtime's own phrasings.
_DEVICE_ERROR = re.compile(
    r"CUDA (error|failed)|CUBLAS_STATUS_\w+|CUDNN_STATUS_\w+|cuDNN|out of memory"
    r"|libcu\w+\.so|device-side assert"
)
_CUDA_LIBRARY = re.compile(r"libcu\w+\.so")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
GPU_STRIKES = 3  # consecutive GPU failures before STT stays on the CPU until restart


def is_device_error(exc: BaseException) -> bool:
    """A failure of the GPU or its libraries (e.g. CUDA out of memory), not of the input.
    An OSError counts only when it names a CUDA library (libcu*.so), so a missing model
    file is not mistaken for a GPU problem."""
    text = str(exc)
    if isinstance(exc, OSError):
        return bool(_CUDA_LIBRARY.search(text))
    return isinstance(exc, RuntimeError) and bool(_DEVICE_ERROR.search(text))


def safe_reason(exc: BaseException) -> str:
    """Text that is safe to log. CTranslate2/CUDA device errors come from the C++ runtime
    and carry no audio or transcript, so their message is kept, with any quoted part
    replaced by <quoted>, as printable ASCII of at most 160 chars. For anything else
    only the exception type is logged."""
    if not is_device_error(exc):
        return type(exc).__name__
    unquoted = _QUOTED.sub("<quoted>", str(exc))
    message = "".join(c if 32 <= ord(c) < 127 else "?" for c in unquoted)[:160]
    return f"{type(exc).__name__}: {message}"


class TranscriptionError(RuntimeError):
    """STT failed on every device it may use. The message carries no user audio or text."""


class FasterWhisperSTT:
    """`SpeechToText` on faster-whisper. Language is chosen among `languages` only.

    GPU failures at request time (e.g. CUDA out of memory when another process holds
    the GPU) fall back to a CPU int8 model for that request. The CPU model is built
    once, on first need, and kept. Later requests try the GPU again; after
    `GPU_STRIKES` consecutive GPU failures the stage stays on the CPU until restart.
    After a fallback both models stay loaded (GPU memory for one, ~0.5 GB of RAM for
    the CPU model) until restart.

    `model_version()` reports the device the last request really used. That state is
    shared by the whole stage, so with several sockets another socket's turn can run
    between this turn's transcription and the moment its telemetry reads the version;
    the recorded device can then belong to that other turn."""

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
        self._state_lock = threading.Lock()  # the server also serialises calls
        self._model: Any = self._load()
        self._loaded_on = self.device  # where the main model lives
        self._cpu_model: Any = self._model if self.device == "cpu" else None
        self._gpu_failures = 0
        self._gpu_disabled = False
        self._used = f"{self.device}:{self.compute_type}"

    def model_version(self) -> str:
        cfg = self.config
        return f"faster-whisper-{cfg.model}@{cfg.revision}:{self._used}"

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
                    raise RuntimeError(f"STT on CUDA failed: {safe_reason(e)}") from e
                log.warning("STT: CUDA unavailable (%s); falling back to CPU int8", safe_reason(e))
        return self._make(WhisperModel, "cpu")

    def _make(self, cls: Any, device: str) -> Any:
        compute = self.config.compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        if device == "cpu" and compute not in ("int8", "float32", "int8_float32"):
            compute = "int8"  # a GPU compute type (e.g. float16) is slow or invalid on CPU
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

    def _cpu(self) -> Any:
        """The CPU int8 model, built on first need and kept."""
        if self._cpu_model is None:
            from faster_whisper import WhisperModel

            device, compute = self.device, self.compute_type
            self._cpu_model = self._make(WhisperModel, "cpu")
            self.device, self.compute_type = device, compute  # the main model is unchanged
        return self._cpu_model

    def transcribe(
        self, audio: AudioChunk, sample_rate: int, *, hotwords: str | None = None
    ) -> Transcript:
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
        with self._state_lock:
            return self._transcribe_with_fallback(samples, hotwords)

    def _transcribe_with_fallback(
        self, samples: NDArray[np.float32], hotwords: str | None = None
    ) -> Transcript:
        if self._loaded_on == "cpu" or self._gpu_disabled:
            return self._decode_on_cpu(
                samples, fallback=self._loaded_on != "cpu", hotwords=hotwords
            )
        try:
            result = self._decode(self._model, samples, hotwords)
        except Exception as e:
            if not is_device_error(e):
                log.error("STT failed on cuda (%s)", safe_reason(e))
                raise TranscriptionError(f"STT failed ({type(e).__name__})") from None
            self._gpu_failures += 1
            if self._gpu_failures >= GPU_STRIKES:
                self._gpu_disabled = True
                log.warning(
                    "STT: GPU failed %d times in a row (%s); using CPU int8 until restart",
                    self._gpu_failures,
                    safe_reason(e),
                )
            else:
                log.warning(
                    "STT: GPU failed (%s); this request uses CPU int8 (%d/%d)",
                    safe_reason(e),
                    self._gpu_failures,
                    GPU_STRIKES,
                )
            return self._decode_on_cpu(samples, fallback=True, hotwords=hotwords)
        self._gpu_failures = 0
        self._used = f"cuda:{self.compute_type}"
        return result

    def _decode_on_cpu(
        self, samples: NDArray[np.float32], fallback: bool, hotwords: str | None = None
    ) -> Transcript:
        try:
            result = self._decode(self._cpu(), samples, hotwords)
        except Exception as e:
            log.error("STT failed on cpu (%s)", safe_reason(e))
            raise TranscriptionError(f"STT failed ({type(e).__name__})") from None
        self._used = "cpu:int8:fallback" if fallback else f"cpu:{self.compute_type}"
        return result

    def _decode(
        self, model: Any, samples: NDArray[np.float32], hotwords: str | None = None
    ) -> Transcript:
        forced = self.languages[0] if len(self.languages) == 1 else None
        text, info, logprob, words = self._run(model, samples, forced, hotwords)
        language = info.language
        if language not in self.languages:
            # Whisper picked a language we don't support (e.g. Hindi for Marathi):
            # re-decode with the most likely supported one.
            probs = dict(info.all_language_probs or [])
            language = max(self.languages, key=lambda lang: probs.get(lang, 0.0))
            text, info, logprob, words = self._run(model, samples, language, hotwords)
        confidence = round(min(1.0, math.exp(logprob)), 3) if text else 0.0
        return Transcript(text=text, language=language, confidence=confidence, words=words)

    def _run(
        self,
        model: Any,
        samples: NDArray[np.float32],
        language: str | None,
        hotwords: str | None = None,
    ) -> tuple[str, Any, float, tuple[Word, ...]]:
        # `hotwords` favours those words in the decoder. For a wake segment it is the
        # wake phrase: alone and without context Whisper otherwise writes a short name
        # as almost anything (measured: 2026-11-16 synthetic run, wake-word-design.md).
        # A wake segment also gets per-word probabilities, so the gate can ask whether
        # the phrase itself was heard, not just the segment on average (false wakes,
        # 2026-11-16). Segments are at most max_segment_s, so seeking is unchanged.
        extra: dict[str, Any] = {"hotwords": hotwords, "word_timestamps": True} if hotwords else {}
        segments, info = model.transcribe(
            samples,
            language=language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            condition_on_previous_text=False,
            **extra,
        )
        parts, weights, total, words = [], 0.0, 0.0, []
        for seg in segments:
            parts.append(seg.text.strip())
            n = max(1, len(seg.tokens))
            weights += n
            total += seg.avg_logprob * n
            words += [
                Word(w.word.strip(), round(float(w.probability), 3), w.start, w.end)
                for w in getattr(seg, "words", None) or ()  # None without word_timestamps
            ]
        text = " ".join(p for p in parts if p)
        return text, info, (total / weights if weights else -math.inf), tuple(words)
