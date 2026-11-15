"""The two sounds of the listener, made in code (no audio files).

`wake`:  it heard its name and is taking the command. Two soft notes going up.
`armed`: it heard only its name and is waiting for the command. One soft, lower note.

Both are plain bell-like tones: a quick soft attack, a smooth decay, a little second
harmonic for warmth. They are quiet (peak about a quarter of full scale), shorter than
a quarter of a second, and sound nothing like speech.
"""

from __future__ import annotations

import math
from array import array
from functools import cache

from sasha.listener.indicator.base import Earcon, EarconKind

SAMPLE_RATE = 24000
PEAK = 0.24
ATTACK_S = 0.008


def _note(freq: float, start_s: float, length_s: float, total: int, out: list[float]) -> None:
    first = int(start_s * SAMPLE_RATE)
    count = int(length_s * SAMPLE_RATE)
    for i in range(count):
        if first + i >= total:
            break
        t = i / SAMPLE_RATE
        attack = 0.5 - 0.5 * math.cos(math.pi * min(1.0, t / ATTACK_S))
        left = 1.0 - i / count
        decay = math.exp(-t * 16.0) * left**0.5  # ends at exactly zero, so no click
        tone = math.sin(2 * math.pi * freq * t) + 0.18 * math.sin(4 * math.pi * freq * t)
        out[first + i] += attack * decay * tone


def _render(notes: list[tuple[float, float, float]], length_s: float) -> Earcon:
    total = int(length_s * SAMPLE_RATE)
    mix = [0.0] * total
    for freq, start_s, note_s in notes:
        _note(freq, start_s, note_s, total, mix)
    top = max(abs(v) for v in mix) or 1.0
    pcm = array("h", (int(v / top * PEAK * 32767) for v in mix))
    return Earcon(pcm=pcm.tobytes() if _little_endian() else _swapped(pcm), sample_rate=SAMPLE_RATE)


def _little_endian() -> bool:
    return array("h", [1]).tobytes() == b"\x01\x00"


def _swapped(pcm: array[int]) -> bytes:
    pcm.byteswap()
    return pcm.tobytes()


@cache
def earcon(kind: EarconKind) -> Earcon:
    if kind == "wake":  # E5 then B5: a rising fifth, "yes?"
        return _render([(659.26, 0.0, 0.13), (987.77, 0.075, 0.145)], 0.22)
    if kind == "armed":  # a single C5: "go on"
        return _render([(523.25, 0.0, 0.18)], 0.18)
    raise ValueError(f"Unknown earcon: {kind!r}")
