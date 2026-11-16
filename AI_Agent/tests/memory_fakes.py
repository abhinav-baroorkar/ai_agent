"""Fakes for the memory tests: a store on a temp file with a fake clock, and a turn."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sasha.config_memory import MemoryConfig
from sasha.conversation import current_turn, new_turn
from sasha.memory.store import MemoryStore
from sasha.memory.tools import MemoryTools
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter
from tests.server_fakes import Harness

START = 2_000_000_000.0  # a fixed fake time (no clock or zone from this machine)


class Clock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_store(
    tmp: Path, telemetry: TelemetryWriter | None = None, **config: Any
) -> tuple[MemoryStore, Clock]:
    clock = Clock()
    store = MemoryStore(MemoryConfig(**config), JsonStore(tmp / "memory.json"), telemetry, clock)
    return store, clock


@contextmanager
def turn(*messages: str, mode: str = "text", asked: bool = False) -> Iterator[None]:
    """A turn whose owner said `messages` (the current one last)."""
    token = current_turn.set(
        new_turn(tuple(messages), mode, asked, None if len(messages) < 2 else 5.0)
    )
    try:
        yield
    finally:
        current_turn.reset(token)


def tools(store: MemoryStore) -> MemoryTools:
    return MemoryTools(store, current_turn.get, "main")


def with_memory(h: Harness, **config: Any) -> tuple[MemoryStore, Clock]:
    clock = Clock()
    cfg = h.settings.profile.memory.model_copy(update=config)
    store = MemoryStore(cfg, JsonStore(h.settings.paths.state_dir / "memory.json"), None, clock)
    h.deps.memory = store
    return store, clock
