"""Silero VAD (v6 ONNX, shipped inside faster-whisper's assets) as a streaming detector.

The model takes 512-sample windows (32 ms at 16 kHz) plus 64 samples of context and
carries its state (`h`, `c`) between calls. Runs on CPU through onnxruntime, one thread.
Imports are lazy so the base package loads without the `voice` extra.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

WINDOW = 512
CONTEXT = 64
STATE_SHAPE = (1, 1, 128)


class VoiceDetector(Protocol):
    def probability(self, window: Any) -> float:
        """Speech probability for one 512-sample float32 window in [-1, 1]."""
        ...

    def reset(self) -> None: ...


def silero_model_path() -> str:
    from faster_whisper.vad import get_assets_path

    path = os.path.join(get_assets_path(), "silero_vad_v6.onnx")
    if not os.path.isfile(path):
        raise FileNotFoundError("Silero VAD model not found in faster-whisper's assets")
    return path


class SileroVad:
    """Streaming Silero VAD. Not thread-safe: one instance per listener."""

    def __init__(self, path: str | None = None) -> None:
        import numpy as np
        import onnxruntime as ort

        self._np = np
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self._session: Any = ort.InferenceSession(
            path or silero_model_path(), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        np = self._np
        self._h = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._c = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)

    def probability(self, window: Any) -> float:
        np = self._np
        samples = np.asarray(window, dtype=np.float32)
        if samples.shape != (WINDOW,):
            raise ValueError(f"VAD window must be {WINDOW} samples")
        batch = np.concatenate([self._context, samples])[None, :]
        out, self._h, self._c = self._session.run(
            None, {"input": batch, "h": self._h, "c": self._c}
        )
        self._context = samples[-CONTEXT:]
        return float(np.asarray(out).ravel()[0])
