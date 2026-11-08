"""Translation stage (Phase 2, IndicTrans2): Marathi <-> English around the LLM."""

from __future__ import annotations

from typing import Protocol


class Translator(Protocol):
    def model_version(self) -> str: ...

    def translate(self, text: str, source: str, target: str) -> str: ...
