"""Vision stage (later phase, e.g. Qwen3-VL swapped in on demand)."""

from __future__ import annotations

from typing import Protocol


class VisionModel(Protocol):
    def model_version(self) -> str: ...

    def describe(self, image: bytes, question: str) -> str: ...
