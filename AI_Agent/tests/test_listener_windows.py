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
    """A ring: the tone closed the mic (`pause_capture`), then `resume_capture` and
    `stop_window` arrive. The window shows listening at once and takes a short "stop"."""

    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: rig.brain.ws is not None and lst.online and rig.mic_open)
        lst.server_paused = True  # the tone plays
        lst._changed()
        await until(lambda: not rig.mic_open)
        before = rig.brain.segments
        lst.server_paused = False  # resume_capture ...
        lst.stop_window.open(2.0)  # ... then stop_window, on the loop thread
        assert rig.indicator.states[-1] == "listening"  # same synchronous step
        assert lst.gate.min_speech_ms == ANSWER_MIN_SPEECH_MS
        await until(lambda: rig.brain.segments == before + 1, 5)  # the short "stop"
        await until(lambda: not lst.stop_window.is_open)
        assert lst.gate.min_speech_ms == MIN_SPEECH_MS

    rig = Rig(pattern=[(30, 400), (3000, 300), (30, 900)])  # a short "stop"
    asyncio.run(rig.run(body))


def test_stop_window_never_overrides_busy() -> None:
    """Review L2: only a confirm window may open the mic during a turn."""

    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: lst.online)
        lst.busy = True
        lst.stop_window.open(2.0)
        assert lst.mic_allowed() == (False, "brain is answering")
        lst.stop_window.close()

    go(body)


def test_timer_stopped_closes_the_stop_window() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: rig.brain.ws is not None and lst.online)
        lst.stop_window.open(5.0)
        msg = {"type": "timer_stopped", "id": "t1", "how": "dismissed"}
        await rig.brain.ws.send(json.dumps(msg))
        await until(lambda: not lst.stop_window.is_open)

    go(body)


def test_the_armed_prime_expires_if_the_mic_does_not_reopen_soon() -> None:
    """Review L1: a reopen long after the tone is a fresh start (300 ms silence first)."""
    import time

    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: lst.online)
        lst.prime_until = time.monotonic() - 0.1  # the tone ended over a second ago
        primed: list[bool] = []
        real_prime = lst.gate.prime
        lst.gate.prime = lambda: primed.append(True) or real_prime()  # type: ignore[method-assign]
        lst.paused = True
        lst._changed()
        await until(lambda: not rig.mic_open)
        lst.paused = False
        lst._changed()
        await until(lambda: rig.mic_open)
        assert primed == [] and lst.prime_until == 0.0

    go(body)


def test_no_recorder_takes_the_mic_sign_down() -> None:
    """Review L3: if no capture program exists, the tray must not keep showing a live mic."""

    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: lst.online)
        await asyncio.sleep(0.2)
        assert rig.indicator.states[-1] not in MIC_OPEN_STATES

    rig = Rig()
    rig.capture = lambda: None  # type: ignore[assignment,method-assign,return-value]
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
