"""The listener with the real tray indicator on a fake session bus."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("websockets")

from sasha.listener.contracts import MIC_OPEN_STATES  # noqa: E402
from sasha.listener.indicator.tray import TrayIndicator  # noqa: E402
from sasha.listener.listener import Listener  # noqa: E402
from tests.indicator_fakes import WHO, FakeBus  # noqa: E402
from tests.listener_rig import Rig, until  # noqa: E402


def _rig_with_tray(bus: FakeBus) -> tuple[Rig, TrayIndicator]:
    rig = Rig()
    tray = TrayIndicator(lambda: bus, WHO)
    rig.indicator = tray  # type: ignore[assignment]
    return rig, tray


def test_mic_follows_the_real_tray_and_stops_when_it_vanishes() -> None:
    bus = FakeBus()
    rig, tray = _rig_with_tray(bus)

    async def reply(ws: Any) -> None:
        await ws.send(json.dumps({"type": "wake", "heard": True, "command": False, "armed_s": 5}))

    async def body(rig: Rig, lst: Listener) -> None:
        rig.brain.on_segment = reply
        await until(lambda: rig.mic_open)
        await until(lambda: tray._state in MIC_OPEN_STATES)
        await until(lambda: rig.brain.segments >= 1)  # earcon from the real indicator played
        proc = rig.captures[-1]
        bus.tray_vanishes()
        await until(lambda: not proc.running, 0.5)
        await asyncio.sleep(0.3)
        assert not rig.mic_open and tray._state not in MIC_OPEN_STATES

    asyncio.run(rig.run(body))


def test_no_tray_watcher_means_no_capture() -> None:
    rig, tray = _rig_with_tray(FakeBus(watcher=False))

    async def body(rig: Rig, lst: Listener) -> None:
        await asyncio.sleep(0.4)
        assert rig.captures == [] and tray._state not in MIC_OPEN_STATES

    asyncio.run(rig.run(body))
