"""Fakes for the listener tests: VAD, guard backend, indicator, capture commands."""

from __future__ import annotations

import asyncio
import math
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any

from sasha.listener.guards import GuardEvent
from sasha.pipeline.tts import SpeechAudio

CHUNK = 640  # 20 ms of 16 kHz s16le


def tone_chunk(amplitude: int, n: int = 0) -> bytes:
    """20 ms of a 220 Hz sine (the fake VAD calls anything loud "speech")."""
    import numpy as np

    t = (np.arange(320) + n * 320) / 16000
    return (amplitude * np.sin(2 * math.pi * 220 * t)).astype("<i2").tobytes()


def noise_chunk(amplitude: int, seed: int = 0) -> bytes:
    import numpy as np

    rng = np.random.default_rng(seed)
    return (rng.normal(0, amplitude, 320)).clip(-32768, 32767).astype("<i2").tobytes()


def chunks(kind: str, ms: int, amplitude: int = 3000) -> list[bytes]:
    n = ms // 20
    if kind == "speech":
        return [tone_chunk(amplitude, i) for i in range(n)]
    return [noise_chunk(amplitude if kind == "noise" else 30, i) for i in range(n)]


class FakeVad:
    """Speech probability from loudness alone: RMS above `threshold` -> 0.9."""

    def __init__(self, threshold: float = 0.02, say: Callable[[Any], float] | None = None):
        self.threshold = threshold
        self.say = say
        self.resets = 0

    def probability(self, window: Any) -> float:
        import numpy as np

        if self.say is not None:
            return self.say(window)
        rms = float(np.sqrt(np.mean(np.asarray(window) ** 2)))
        return 0.9 if rms > self.threshold else 0.05

    def reset(self) -> None:
        self.resets += 1


class FakeBackend:
    """Guard facts set by the test; `fail` names a call that raises."""

    def __init__(self) -> None:
        self.locked = False
        self.active = True
        self.battery = False
        self.fail: set[str] = set()
        self.events_q: asyncio.Queue[GuardEvent] = asyncio.Queue()
        self.inhibitor = False
        self.log: list[str] = []

    def _check(self, name: str) -> None:
        if name in self.fail:
            raise RuntimeError(f"fake {name} failure")

    async def connect(self) -> None:
        self._check("connect")

    async def screen_locked(self) -> bool:
        self._check("lock")
        return self.locked

    async def session_active(self) -> bool:
        self._check("active")
        return self.active

    async def on_battery(self) -> bool:
        self._check("battery")
        return self.battery

    def take_sleep_inhibitor(self) -> None:
        self._check("inhibit")
        self.inhibitor = True
        self.log.append("take")

    def release_sleep_inhibitor(self) -> None:
        self.inhibitor = False
        self.log.append("release")

    async def events(self) -> AsyncIterator[GuardEvent]:
        self._check("subscribe")
        while True:
            yield await self.events_q.get()

    async def close(self) -> None:
        self.log.append("close")


class FakeIndicator:
    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.states: list[str] = []
        self.on_pause_toggle: Callable[[], None] | None = None
        self.on_lost: Callable[[], None] | None = None
        self.stopped = False

    def start(self, on_pause_toggle: Callable[[], None], on_lost: Callable[[], None]) -> None:
        self.on_pause_toggle, self.on_lost = on_pause_toggle, on_lost

    def available(self) -> bool:
        return self._available

    def set_state(self, state: str) -> None:
        if not self.states or self.states[-1] != state:
            self.states.append(state)

    def earcon(self, kind: str) -> SpeechAudio:
        return SpeechAudio(pcm=b"\x01\x00" * 160, sample_rate=16000)

    def stop(self) -> None:
        self.stopped = True

    def lose(self) -> None:
        self._available = False
        assert self.on_lost is not None
        self.on_lost()


def fake_mic_command(seconds: float = 30.0, riff: bool = False) -> list[str]:
    """A stand-in for arecord: writes zeros (optionally behind a WAV header) to stdout."""
    code = (
        "import sys, time\n"
        f"riff = {riff!r}\n"
        "out = sys.stdout.buffer\n"
        "if riff:\n"
        "    out.write(b'RIFF' + b'\\xff' * 4 + b'WAVEfmt ' + b'\\x10\\0\\0\\0' + b'\\0' * 16"
        " + b'data' + b'\\xff' * 4)\n"
        "end = time.time() + " + repr(seconds) + "\n"
        "while time.time() < end:\n"
        "    out.write(b'\\x01\\x00' * 320); out.flush(); time.sleep(0.02)\n"
    )
    return [sys.executable, "-c", code]
