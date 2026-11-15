"""One-segment windows (confirm, stop) and earcon half-duplex."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("websockets")

from sasha.listener.capture import CaptureProcess  # noqa: E402
from sasha.listener.confirm import ConfirmWindow, StopWindow  # noqa: E402
from sasha.listener.contracts import MIC_OPEN_STATES  # noqa: E402
from sasha.listener.gate import ANSWER_MIN_SPEECH_MS, MIN_SPEECH_MS  # noqa: E402
from sasha.listener.listener import Listener  # noqa: E402
from tests.listener_rig import Rig, go, until  # noqa: E402


def test_a_slow_question_sends_an_early_window_back_to_pending() -> None:
    """Review C1: the window opened on the grace timer; then the question's audio
    starts. It must wait for that speech and then reopen with a fresh timer."""

    async def main() -> None:
        changes: list[str] = []
        win = ConfirmWindow(lambda: changes.append(win.state))
        win.announce(1.0, speech_follows=lambda: False)
        await asyncio.sleep(1.7)  # grace (1.5 s) passed: opened early
        assert win.is_open
        win.speech_started()  # the question's tts_start arrives late
        assert win.pending
        await asyncio.sleep(1.2)  # longer than timeout_s: an old timer would have closed it
        assert win.pending
        win.speech_done()
        assert win.is_open
        await asyncio.sleep(0.6)
        assert win.is_open  # fresh 1 s timer
        await asyncio.sleep(0.6)
        assert not win.is_open
        await win.aclose()

    asyncio.run(main())


def test_stop_window_opens_only_on_the_event_and_closes() -> None:
    async def main() -> None:
        win = StopWindow(lambda: None)
        assert not win.is_open
        win.open(0.5)
        assert win.is_open
        await asyncio.sleep(0.7)
        assert not win.is_open  # timeout
        win.open(99)  # capped at 8 s
        win.close()
        assert not win.is_open

    asyncio.run(main())


def test_stop_window_shows_listening_in_the_same_step_and_takes_one_short_segment() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: rig.brain.ws is not None and lst.online)
        lst.busy = True  # e.g. still answering: only the window may open the mic
        lst._changed()
        await until(lambda: not rig.mic_open)
        before = rig.brain.segments
        lst.stop_window.open(2.0)  # what `stop_window` does, on the loop thread
        assert rig.indicator.states[-1] == "listening"  # same synchronous step
        assert lst.gate.min_speech_ms == ANSWER_MIN_SPEECH_MS
        await until(lambda: rig.brain.segments == before + 1, 5)
        await until(lambda: not lst.stop_window.is_open)
        await until(lambda: not rig.mic_open)  # still busy: closed again
        assert lst.gate.min_speech_ms == MIN_SPEECH_MS

    rig = Rig(pattern=[(30, 400), (3000, 300), (30, 900)])  # a short "stop"
    asyncio.run(rig.run(body))


def test_the_next_tone_closes_the_stop_window() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: rig.brain.ws is not None and lst.online)
        lst.stop_window.open(5.0)
        await rig.brain.ws.send(json.dumps({"type": "tts_start", "sample_rate": 24000}))
        await until(lambda: not lst.stop_window.is_open)
        await rig.brain.ws.send(json.dumps({"type": "tts_end"}))

    go(body)


def test_armed_earcon_is_half_duplex_and_a_quick_command_survives() -> None:
    """Review N3: the mic is closed while the "armed" tone plays (+300 ms), and a
    command that starts the moment the mic reopens is still taken (the gate is primed:
    the pause was our own tone, not someone's sentence)."""
    count = {"n": 0}

    async def reply(ws: Any) -> None:
        count["n"] += 1
        if count["n"] == 1:
            await ws.send(
                json.dumps({"type": "wake", "heard": True, "command": False, "armed_s": 5})
            )

    async def body(rig: Rig, lst: Listener) -> None:
        rig.brain.on_segment = reply
        await until(lambda: rig.brain.segments >= 1)
        await until(lambda: lst.playing, 2)  # the earcon plays with the mic closed
        assert not rig.mic_open
        await until(lambda: rig.brain.segments >= 2, 5)  # the quick command arrived

    rig = Rig()
    real = rig.capture
    calls = {"n": 0}

    def capture() -> CaptureProcess:  # after the tone, speech starts at once
        calls["n"] += 1
        if calls["n"] > 1:
            rig.pattern = [(3000, 700), (30, 900)]
        return real()

    rig.capture = capture  # type: ignore[method-assign]
    asyncio.run(rig.run(body))
    assert set(rig.indicator.states) & MIC_OPEN_STATES
