"""Interfaces and settings the listener's parts share."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sasha.listener.gate import END_SILENCE_MS, Segment
from sasha.listener.indicator.base import MIC_OPEN, Earcon, Indicator, State

RECONNECT_S = (1.0, 2.0, 5.0, 10.0, 30.0)
CHECK_S = 2.0
# The indicator states in which the microphone may be open: the capture child is
# alive only while one of these is shown.
MIC_OPEN_STATES = MIC_OPEN

__all__ = [
    "CHECK_S",
    "MIC_OPEN_STATES",
    "RECONNECT_S",
    "Connect",
    "Earcon",
    "Indicator",
    "ListenerSettings",
    "SegmentSink",
    "State",
]


@dataclass
class ListenerSettings:
    ws_url: str
    pause_on_battery: bool = True
    end_silence_ms: int = END_SILENCE_MS
    check_s: float = CHECK_S
    reconnect_s: tuple[float, ...] = RECONNECT_S


Connect = Callable[[str], Any]  # url -> async context manager yielding a websocket
SegmentSink = Callable[[Segment], None]  # the debug dump (CLI flag only)
