"""The tray icons, drawn in code and handed to the tray as pixel data.

No icon files are installed anywhere. Each icon is a microphone glyph in the colour
language of the window's orb, with a dark edge so it reads on light and dark panels,
and a few points of light around it. Shapes are described by distance functions in a
unit square and rendered with smooth edges at every size the tray may ask for.

The glyphs: `waiting` is a live amber microphone; `listening` teal with sound arcs;
`thinking` violet with three dots; `speaking` a dark microphone on a filled amber
disc, so it can't be mistaken for `waiting`; `paused` a pale microphone struck through
in red; `offline` grey, struck through, with a broken line; `debug` red with a large
red dot, because audio is being saved.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import cache

from sasha.listener.indicator.base import State

Color = tuple[int, int, int]
Distance = Callable[[float, float], float]  # signed distance to the shape edge, unit square
Shape = tuple[Distance, Color]
Pixmap = tuple[int, int, bytes]  # width, height, ARGB32 in network byte order

SIZES = (16, 22, 24, 32, 44, 48)
EDGE: Color = (10, 13, 20)
EDGE_WIDTH = 0.045
EDGE_ALPHA = 0.7

AMBER: Color = (243, 186, 96)
TEAL: Color = (78, 214, 194)
VIOLET: Color = (172, 158, 247)
PALE: Color = (216, 220, 226)
GREY: Color = (138, 144, 154)
STRIKE: Color = (244, 108, 88)
RED: Color = (255, 78, 62)
WHITE: Color = (255, 255, 255)
INK: Color = (52, 32, 6)  # the dark microphone drawn on the amber disc


def capsule(ax: float, ay: float, bx: float, by: float, r: float) -> Distance:
    """A line from a to b with round ends and half-width r."""
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy or 1e-9

    def dist(x: float, y: float) -> float:
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length2))
        return math.hypot(x - (ax + t * dx), y - (ay + t * dy)) - r

    return dist


def dot(cx: float, cy: float, r: float) -> Distance:
    return lambda x, y: math.hypot(x - cx, y - cy) - r


def arc(cx: float, cy: float, r: float, start: float, end: float, half: float) -> Distance:
    """Part of a ring from angle start to end (radians, y pointing down), half-width half."""
    ends = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in (start, end)]

    def dist(x: float, y: float) -> float:
        a = math.atan2(y - cy, x - cx)
        while a < start:
            a += 2 * math.pi
        if a <= end:
            return abs(math.hypot(x - cx, y - cy) - r) - half
        return min(math.hypot(x - ex, y - ey) for ex, ey in ends) - half

    return dist


def microphone(color: Color) -> list[Shape]:
    return [
        (capsule(0.5, 0.23, 0.5, 0.49, 0.135), color),  # the head
        (arc(0.5, 0.46, 0.255, 0.0, math.pi, 0.04), color),  # the cradle under it
        (capsule(0.5, 0.72, 0.5, 0.84, 0.04), color),  # the stem
        (capsule(0.37, 0.87, 0.63, 0.87, 0.04), color),  # the foot
    ]


def _lighter(c: Color, k: float = 0.45) -> Color:
    return (
        int(c[0] + (255 - c[0]) * k),
        int(c[1] + (255 - c[1]) * k),
        int(c[2] + (255 - c[2]) * k),
    )


def glyph(state: State) -> list[Shape]:
    """The shapes of one state's icon, back to front."""
    if state == "waiting":
        sparks = _lighter(AMBER)
        return [
            *microphone(AMBER),
            (dot(0.86, 0.2, 0.06), sparks),
            (dot(0.91, 0.42, 0.04), sparks),
            (dot(0.13, 0.3, 0.045), sparks),
        ]
    if state == "listening":
        return [
            *microphone(TEAL),
            (arc(0.5, 0.38, 0.42, -0.6, 0.6, 0.035), TEAL),
            (arc(0.5, 0.38, 0.42, math.pi - 0.6, math.pi + 0.6, 0.035), TEAL),
        ]
    if state == "thinking":
        return [
            *microphone(VIOLET),
            (dot(0.84, 0.14, 0.06), VIOLET),
            (dot(0.91, 0.34, 0.05), VIOLET),
            (dot(0.86, 0.53, 0.04), VIOLET),
        ]
    if state == "speaking":
        # a dark microphone on a filled, brighter amber disc: a different outline from
        # "waiting", so the two can't be confused even at 22 px
        return [(dot(0.5, 0.5, 0.46), _lighter(AMBER, 0.25)), *microphone(INK)]
    if state == "paused":
        return [*microphone(PALE), (capsule(0.14, 0.12, 0.86, 0.88, 0.055), STRIKE)]
    if state == "offline":
        return [
            *microphone(GREY),
            (capsule(0.14, 0.12, 0.86, 0.88, 0.05), _lighter(GREY, 0.3)),
            (capsule(0.72, 0.16, 0.78, 0.16, 0.035), GREY),
            (capsule(0.88, 0.16, 0.94, 0.16, 0.035), GREY),
        ]  # fmt: skip
    if state == "debug":
        return [*microphone(RED), (dot(0.8, 0.22, 0.2), WHITE), (dot(0.8, 0.22, 0.15), RED)]
    raise ValueError(f"Unknown indicator state: {state!r}")


def render(shapes: list[Shape], size: int) -> bytes:
    """ARGB32 pixels (network byte order) for the shapes at size x size."""
    out = bytearray(size * size * 4)
    for py in range(size):
        y = (py + 0.5) / size
        for px in range(size):
            x = (px + 0.5) / size
            a = r = g = b = 0.0
            # the dark edge of every shape first, then the shapes themselves
            passes = [(d, EDGE, EDGE_WIDTH, EDGE_ALPHA) for d, _ in shapes] + [
                (d, c, 0.0, 1.0) for d, c in shapes
            ]
            for dist, color, grow, strength in passes:
                cover = max(0.0, min(1.0, 0.5 - (dist(x, y) - grow) * size)) * strength
                if cover <= 0.0:
                    continue
                r = r * (1 - cover) + color[0] * cover
                g = g * (1 - cover) + color[1] * cover
                b = b * (1 - cover) + color[2] * cover
                a = a + (1 - a) * cover
            i = (py * size + px) * 4
            out[i : i + 4] = bytes((round(a * 255), round(r), round(g), round(b)))
    return bytes(out)


@cache
def pixmaps(state: State) -> tuple[Pixmap, ...]:
    """Every size of one state's icon, ready for the tray's `IconPixmap`."""
    shapes = glyph(state)
    return tuple((size, size, render(shapes, size)) for size in SIZES)
