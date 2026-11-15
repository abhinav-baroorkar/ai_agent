"""Listener turns: the visible-sign invariant, confirmation, drops, pause order."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

np = pytest.importorskip("numpy")
websockets = pytest.importorskip("websockets")

from sasha.listener.capture import CaptureProcess  # noqa: E402
from sasha.listener.contracts import MIC_OPEN_STATES  # noqa: E402
from sasha.listener.guards import GuardEvent  # noqa: E402
from sasha.listener.listener import Listener  # noqa: E402
from tests.listener_fakes import FakeIndicator  # noqa: E402
from tests.listener_rig import Rig, go, until  # noqa: E402


class WatchedIndicator(FakeIndicator):
    """Fails the test if the visible-sign invariant is ever broken."""

    def __init__(self, rig: Rig) -> None:
        super().__init__()
        self.rig = rig
        self.violations: list[str] = []

    def set_state(self, state: str) -> None:
        if state not in MIC_OPEN_STATES and any(p.running for p in self.rig.captures):
            self.violations.append(f"{state} shown while the capture child runs")
        super().set_state(state)


def _watched() -> Rig:
    rig = Rig()
    rig.indicator = WatchedIndicator(rig)
    real = rig.capture

    def capture() -> CaptureProcess:
        proc = real()
        start = proc.start

        async def checked_start() -> None:
            last = rig.indicator.states[-1] if rig.indicator.states else None
            if last not in MIC_OPEN_STATES:
                rig.indicator.violations.append(f"capture started while showing {last}")  # type: ignore[attr-defined]
            await start()

        proc.start = checked_start  # type: ignore[method-assign]
        return proc

    rig.capture = capture  # type: ignore[method-assign]
    return rig


def test_capture_runs_only_while_a_mic_open_state_is_shown() -> None:
    """Review L2: every transition of a full turn keeps the invariant."""

    async def reply(ws: Any) -> None:
        for msg in (
            {"type": "wake", "heard": True, "command": True},
            {"type": "state", "state": "thinking"},
            {"type": "tts_start", "sample_rate": 24000},
        ):
            await ws.send(json.dumps(msg))
        await ws.send(b"\x01\x00" * 480)
        await ws.send(json.dumps({"type": "tts_end"}))
        await ws.send(json.dumps({"type": "state", "state": "idle"}))

    async def body(rig: Rig, lst: Listener) -> None:
        rig.brain.on_segment = reply
        await until(lambda: rig.brain.segments >= 1)
        await until(lambda: lst.busy or lst.playing)
        assert not rig.mic_open  # closed while the brain answers (busy)
        await until(lambda: not lst.busy and not lst.playing and rig.mic_open)
        rig.backend.events_q.put_nowait(GuardEvent("lock", True))
        rig.backend.locked = True
        await until(lambda: not rig.mic_open)
        await asyncio.sleep(0.1)

    rig = _watched()
    asyncio.run(rig.run(body))
    assert rig.indicator.violations == []  # type: ignore[attr-defined]
    assert {"thinking", "speaking", "waiting", "paused"} <= set(rig.indicator.states)


def _confirm_brain(answer: bool) -> Callable[[Any], Awaitable[None]]:
    count = {"n": 0}

    async def reply(ws: Any) -> None:
        count["n"] += 1
        if count["n"] == 1:  # the command: the brain asks for confirmation
            for msg in (
                {"type": "wake", "heard": True, "command": True},
                {"type": "state", "state": "thinking"},
                {"type": "confirm", "action": "turn off all lights", "timeout_s": 1.0},
                {"type": "tts_start", "sample_rate": 24000},
            ):
                await ws.send(json.dumps(msg))
            await ws.send(b"\x01\x00" * 480)
            await ws.send(json.dumps({"type": "tts_end"}))
        elif count["n"] == 2:  # the answer segment
            await ws.send(json.dumps({"type": "confirm_result", "accepted": True, "reason": "yes"}))
            await ws.send(json.dumps({"type": "state", "state": "idle"}))

    return reply


def test_confirm_then_yes() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        rig.brain.on_segment = _confirm_brain(True)
        await until(lambda: rig.brain.segments >= 1)
        await until(lambda: lst.confirm.is_open, 5)
        assert lst.busy  # the turn is still running; only the window lets the mic open
        await until(lambda: rig.mic_open)
        assert rig.indicator.states[-1] == "listening"
        await until(lambda: rig.brain.segments >= 2, 5)  # one segment, sent as wake audio
        await until(lambda: not lst.confirm.is_open)
        await until(lambda: not lst.busy)

    rig = go(body)
    starts = [m for m in rig.brain.received if isinstance(m, dict) and m["type"] == "audio_start"]
    assert starts[1] == {"type": "audio_start", "sample_rate": 16000, "wake": True}


def test_confirm_then_silence_closes_the_window() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        rig.brain.on_segment = _confirm_brain(True)
        await until(lambda: rig.brain.segments >= 1)
        await until(lambda: lst.confirm.is_open, 5)
        await until(lambda: not lst.confirm.is_open, 3)  # timeout_s = 1 s
        await asyncio.sleep(0.2)
        assert rig.brain.segments == 1 and not rig.mic_open  # still busy: mic closed

    rig = Rig(pattern=[(30, 600), (3000, 1000), (30, 900)])
    real = rig.capture
    calls = {"n": 0}

    def capture() -> CaptureProcess:  # the second microphone hears only silence
        calls["n"] += 1
        if calls["n"] > 1:
            rig.pattern = [(30, 100)]
        return real()

    rig.capture = capture  # type: ignore[method-assign]
    asyncio.run(rig.run(body))


def test_socket_drop_mid_reply_does_not_leave_the_mic_closed() -> None:
    """Review L5."""

    async def reply(ws: Any) -> None:
        await ws.send(json.dumps({"type": "tts_start", "sample_rate": 24000}))
        await ws.close()

    async def body(rig: Rig, lst: Listener) -> None:
        rig.brain.on_segment = reply
        await until(lambda: rig.brain.segments >= 1)
        await until(lambda: rig.brain.connections >= 2 and lst.online)
        assert not lst.playing
        await until(lambda: rig.mic_open)

    go(body)


def test_pause_shows_paused_in_the_same_step_as_the_kill() -> None:
    """The indicator reads the pause direction from the last set_state: the kill and
    the `paused` state happen in one synchronous call, before the loop runs again."""

    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: rig.mic_open)
        proc = rig.captures[-1]
        lst._toggle_pause()  # what the loop runs for the indicator's callback
        assert not proc.running and rig.indicator.states[-1] == "paused"

    go(body)


def test_pause_while_offline_still_shows_paused() -> None:
    """The indicator decides pause/resume from the last state: a pause must read as
    `paused`, not `offline`, even with the brain down."""

    async def body(rig: Rig, lst: Listener) -> None:
        await asyncio.sleep(0.2)
        assert rig.indicator.states[-1] == "offline"
        lst._toggle_pause()
        assert rig.indicator.states[-1] == "paused"
        lst._toggle_pause()
        assert rig.indicator.states[-1] == "offline"

    go(body, healthy=False)
