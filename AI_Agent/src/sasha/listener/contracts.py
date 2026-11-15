"""Interfaces and settings the listener's parts share."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sasha.listener.gate import END_SILENCE_MS, Segment

RECONNECT_S = (1.0, 2.0, 5.0, 10.0, 30.0)
CHECK_S = 2.0
# Indicator states in which the microphone may be open (mirrors the indicator's own
# `MIC_OPEN`): the capture child is alive only while one of these is shown.
MIC_OPEN_STATES = frozenset({"waiting", "listening", "debug"})


class Clip(Protocol):
    """An earcon: 16-bit little-endian mono PCM."""

    pcm: bytes
    sample_rate: int


class Indicator(Protocol):
    """Built by the design session (`sasha.listener.indicator`). Its callbacks run on its
    own thread; `available()` blocks up to 1.5 s, so it is only called from the 2 s
    check, in a worker thread, never on the event loop or per audio chunk."""

    def start(self, on_pause_toggle: Callable[[], None], on_lost: Callable[[], None]) -> None: ...

    def available(self) -> bool: ...

    def set_state(self, state: str) -> None: ...

    def earcon(self, kind: str) -> Clip: ...

    def stop(self) -> None: ...


@dataclass
class ListenerSettings:
    ws_url: str
    pause_on_battery: bool = True
    end_silence_ms: int = END_SILENCE_MS
    check_s: float = CHECK_S
    reconnect_s: tuple[float, ...] = RECONNECT_S


Connect = Callable[[str], Any]  # url -> async context manager yielding a websocket
SegmentSink = Callable[[Segment], None]  # the debug dump (CLI flag only)
