"""STT: a GPU failure at request time falls back to CPU; logs stay free of user text."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from sasha.config import STTConfig

np = pytest.importorskip("numpy")

from sasha.pipeline import stt_whisper  # noqa: E402
from sasha.pipeline.stt_whisper import (  # noqa: E402
    GPU_STRIKES,
    FasterWhisperSTT,
    TranscriptionError,
    is_device_error,
    safe_reason,
)

OOM = "CUDA failed with error out of memory"
AUDIO = b"\x01\x00" * 16000  # 1 s
SECRET = "my bank PIN is 4321"


@dataclass
class _Seg:
    text: str
    tokens: list[int] = field(default_factory=lambda: [1, 2])
    avg_logprob: float = -0.1


@dataclass
class _Info:
    language: str = "en"
    all_language_probs: list[tuple[str, float]] = field(default_factory=lambda: [("en", 0.9)])


class FakeModel:
    """Stands in for faster_whisper.WhisperModel. `fail` is raised while decoding."""

    def __init__(self, text: str, fail: list[BaseException | None] | None = None) -> None:
        self.text = text
        self.fail = list(fail or [])
        self.calls = 0

    def transcribe(self, samples: Any, **kwargs: Any) -> tuple[Any, _Info]:
        self.calls += 1
        error = self.fail.pop(0) if self.fail else None

        def segments() -> Any:
            if error is not None:  # faster-whisper decodes lazily: errors come here
                raise error
            yield _Seg(self.text)

        return segments(), _Info()


def _stt(gpu: FakeModel, cpu: FakeModel, monkeypatch: pytest.MonkeyPatch) -> FasterWhisperSTT:
    stt = FasterWhisperSTT.__new__(FasterWhisperSTT)
    stt.config = STTConfig()
    stt.languages = ["en"]
    stt.device, stt.compute_type = "cuda", "float16"
    stt._state_lock = threading.Lock()
    stt._model, stt._loaded_on, stt._cpu_model = gpu, "cuda", None
    stt._gpu_failures, stt._gpu_disabled, stt._used = 0, False, "cuda:float16"
    built: list[str] = []

    def make(cls: Any, device: str) -> FakeModel:
        built.append(device)
        stt.device, stt.compute_type = device, "int8"
        return cpu

    monkeypatch.setattr(stt, "_make", make)
    monkeypatch.setattr("faster_whisper.WhisperModel", object, raising=False)
    stt.built = built  # type: ignore[attr-defined]
    return stt


def test_device_errors_are_recognised() -> None:
    assert is_device_error(RuntimeError(OOM))
    assert is_device_error(RuntimeError("cuBLAS failed with status CUBLAS_STATUS_ALLOC_FAILED"))
    assert is_device_error(OSError("Library libcublas.so.12 is not found or cannot be loaded"))
    assert not is_device_error(RuntimeError("something else"))
    assert not is_device_error(ValueError(OOM))
    # Review S1: words like "GPU" in user text, or a missing file, are not device errors.
    assert not is_device_error(
        RuntimeError("Invalid token in 'please switch on the GPU fan in the study'")
    )
    assert not is_device_error(
        FileNotFoundError(2, "No such file or directory", "/models/voice-gpu/config.json")
    )
    assert is_device_error(RuntimeError("CUDA error: device-side assert triggered"))
    assert is_device_error(RuntimeError("cuDNN failed with status CUDNN_STATUS_INTERNAL_ERROR"))


def test_safe_reason_keeps_device_messages_only() -> None:
    assert safe_reason(RuntimeError(OOM)) == f"RuntimeError: {OOM}"
    assert safe_reason(ValueError(SECRET)) == "ValueError"
    assert safe_reason(RuntimeError(SECRET)) == "RuntimeError"
    quoted = safe_reason(RuntimeError(f"CUDA failed with error out of memory near '{SECRET}'"))
    assert quoted == "RuntimeError: CUDA failed with error out of memory near <quoted>"
    double = safe_reason(RuntimeError(f'CUDA error: bad value "{SECRET}" in kernel'))
    assert "4321" not in double and "<quoted>" in double
    long = safe_reason(RuntimeError("CUDA error " + "x" * 500 + "\né"))
    assert len(long) <= len("RuntimeError: ") + 160 and "\n" not in long
    assert long.startswith("RuntimeError: CUDA error xxx")  # still a device error


def test_oom_falls_back_to_cpu_for_that_request(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    gpu, cpu = FakeModel("gpu text", fail=[RuntimeError(OOM)]), FakeModel("cpu text")
    stt = _stt(gpu, cpu, monkeypatch)
    with caplog.at_level(logging.WARNING, logger=stt_whisper.__name__):
        assert stt.transcribe(AUDIO, 16000).text == "cpu text"
    assert stt.model_version().endswith(":cpu:int8:fallback")
    assert OOM in caplog.text and "(1/3)" in caplog.text
    # Next request: GPU again, CPU model kept (built once).
    assert stt.transcribe(AUDIO, 16000).text == "gpu text"
    assert stt.model_version().endswith(":cuda:float16")
    gpu.fail = [RuntimeError(OOM)]
    assert stt.transcribe(AUDIO, 16000).text == "cpu text"
    assert stt.built == ["cpu"]  # type: ignore[attr-defined]


def test_three_strikes_stay_on_cpu(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    gpu = FakeModel("gpu text", fail=[RuntimeError(OOM)] * GPU_STRIKES)
    stt = _stt(gpu, FakeModel("cpu text"), monkeypatch)
    with caplog.at_level(logging.WARNING, logger=stt_whisper.__name__):
        for _ in range(GPU_STRIKES):
            assert stt.transcribe(AUDIO, 16000).text == "cpu text"
    assert "3 times in a row" in caplog.text and "until restart" in caplog.text
    calls = gpu.calls
    assert stt.transcribe(AUDIO, 16000).text == "cpu text"  # GPU not tried again
    assert gpu.calls == calls
    assert stt.model_version().endswith(":cpu:int8:fallback")


def test_a_success_resets_the_strike_count(monkeypatch: pytest.MonkeyPatch) -> None:
    gpu = FakeModel("gpu", fail=[RuntimeError(OOM), RuntimeError(OOM), None, RuntimeError(OOM)])
    stt = _stt(gpu, FakeModel("cpu"), monkeypatch)
    results = [stt.transcribe(AUDIO, 16000).text for _ in range(5)]
    assert results == ["cpu", "cpu", "gpu", "cpu", "gpu"]
    assert not stt._gpu_disabled


def test_non_device_errors_are_not_retried_and_hide_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    gpu = FakeModel("gpu", fail=[ValueError(f"bad token in {SECRET}")])
    stt = _stt(gpu, FakeModel("cpu"), monkeypatch)
    with caplog.at_level(logging.DEBUG), pytest.raises(TranscriptionError) as info:
        stt.transcribe(AUDIO, 16000)
    assert "4321" not in str(info.value) and "4321" not in caplog.text
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert stt.built == []  # type: ignore[attr-defined]


def test_cpu_failure_after_fallback_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    gpu = FakeModel("gpu", fail=[RuntimeError(OOM)])
    cpu = FakeModel("cpu", fail=[RuntimeError("std::bad_alloc")])
    stt = _stt(gpu, cpu, monkeypatch)
    with pytest.raises(TranscriptionError, match=r"STT failed \(RuntimeError\)"):
        stt.transcribe(AUDIO, 16000)


def test_fallback_turn_is_recorded_in_telemetry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from sasha.server.warmup import PlacementTelemetry

    gpu = FakeModel("gpu", fail=[RuntimeError(OOM)])
    stt = _stt(gpu, FakeModel("cpu"), monkeypatch)
    writer = PlacementTelemetry(tmp_path, "a", "p", enabled=False)
    writer.voice_models = {"stt": stt.model_version}
    stt.transcribe(AUDIO, 16000)  # the server transcribes before the trace starts
    assert writer.start(input_mode="voice").models["stt"].endswith(":cpu:int8:fallback")
    stt.transcribe(AUDIO, 16000)
    assert writer.start(input_mode="voice").models["stt"].endswith(":cuda:float16")
