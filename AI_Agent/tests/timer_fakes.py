"""Fakes for timer tests: a fake clock and an invented time zone (team rule: never this
machine's clock or zone), plus sockets that record what the hub sends them."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

from sasha.config_timers import TimersConfig
from sasha.server.timers.hub import Hub
from sasha.server.timers.service import TimerService
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter


class ToyZone(tzinfo):
    """An invented zone, "TOY": UTC+02:30, and +03:30 (summer) between two made-up
    dates in 2031. Wall times in the spring gap are read as standard time."""

    STD = timedelta(hours=2, minutes=30)
    SUMMER = timedelta(hours=1)
    START = datetime(2031, 3, 30, 2, 0)  # standard wall time when summer starts
    END = datetime(2031, 10, 26, 2, 0)  # standard time when it ends

    def _summer_wall(self, wall: datetime) -> bool:
        return self.START + self.SUMMER <= wall < self.END + self.SUMMER

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self.STD + self.dst(dt)

    def dst(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(0)
        return self.SUMMER if self._summer_wall(dt.replace(tzinfo=None)) else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "TOYS" if dt is not None and self.dst(dt) else "TOY"

    def fromutc(self, dt: datetime) -> datetime:
        std = dt.replace(tzinfo=None) + self.STD
        if self.START <= std < self.END:
            return (std + self.SUMMER).replace(tzinfo=self)
        return std.replace(tzinfo=self)

    def __repr__(self) -> str:
        return "ToyZone()"

    def __str__(self) -> str:
        return "TOY"


ZONE = ToyZone()
WEST = timezone(timedelta(hours=-4, minutes=-15), "TOYWEST")  # invented, for a move


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=ZONE).timestamp()


START = at(2031, 3, 27, 6, 0)  # a Thursday, 06:00 TOY


class FakeClock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTarget:
    """A socket as the hub sees it: frames and rings recorded."""

    def __init__(self, role: str = "home") -> None:
        self.ring_role = role
        self.frames: list[dict[str, Any]] = []
        self.rings: list[tuple[str, str, bool]] = []
        self.unchecked: list[str] = []  # rings sent with check=False (lines, say)
        self.stops: list[str] = []

    def push_threadsafe(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    def ring_threadsafe(self, item_id: str, say: str, tone: bool, check: bool = True) -> None:
        self.rings.append((item_id, say, tone))
        if not check:
            self.unchecked.append(item_id)

    def stop_ring_threadsafe(self, item_id: str) -> None:
        self.stops.append(item_id)

    def types(self) -> list[str]:
        return [f["type"] for f in self.frames]


def make_service(
    tmp: Path,
    clock: FakeClock | None = None,
    telemetry: TelemetryWriter | None = None,
    **config: Any,
) -> tuple[TimerService, Hub, FakeClock]:
    clock = clock or FakeClock()
    cfg = TimersConfig(**config)
    service = TimerService(cfg, JsonStore(tmp / "timers.json"), telemetry, clock, zone=ZONE)
    hub = Hub(cfg)
    hub.service, service.hub = service, hub
    return service, hub, clock
