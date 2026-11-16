"""sin, cos and tan in degrees for the calculator (review S3).

The angle is reduced mod 360 in Decimal first, so a huge angle keeps its digits. By
Niven's theorem the only rational sine and cosine values at rational degrees are 0,
±1/2 and ±1, and the only rational tangents 0 and ±1: those angles get exact results
(and true zeros are checked in Decimal, never snapped from a small float). Every other
angle goes through a float and is marked approximate.
"""

from __future__ import annotations

import math
from decimal import Decimal

HALF = Decimal("0.5")
EXACT: dict[str, dict[int, Decimal | int]] = {
    "sin": {0: 0, 30: HALF, 90: 1, 150: HALF, 180: 0, 210: -HALF, 270: -1, 330: -HALF},
    "cos": {0: 1, 60: HALF, 90: 0, 120: -HALF, 180: -1, 240: -HALF, 270: 0, 300: HALF},
    "tan": {0: 0, 45: 1, 135: -1, 180: 0, 225: 1, 315: -1},
}
MAX_ANGLE = Decimal(10) ** 30


class TrigError(ValueError):
    pass


def trig(name: str, x: Decimal) -> tuple[Decimal, bool]:
    """(value, approximate) for `name` of `x` degrees."""
    if abs(x) > MAX_ANGLE:
        raise TrigError("Angle too large")
    angle = x % 360
    if angle < 0:
        angle += 360
    if name == "tan" and angle % 180 == 90:
        raise TrigError("tan is undefined there")
    if angle == angle.to_integral_value() and int(angle) in EXACT[name]:
        return Decimal(EXACT[name][int(angle)]), False
    value = getattr(math, name)(math.radians(float(angle)))
    return Decimal(f"{value:.15g}"), True
