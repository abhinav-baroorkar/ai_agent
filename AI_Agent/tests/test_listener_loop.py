"""The listener main loop against a fake brain (a real local WebSocket server)."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

np = pytest.importorskip("numpy")
websockets = pytest.importorskip("websockets")

from sasha.listener.brain import BrainHealth  # noqa: E402
from sasha.listener.capture import CaptureProcess  # noqa: E402
from sasha.listener.gate import Gate  # noqa: E402
from sasha.listener.guards import GuardEvent, Guards  # noqa: E402
from sasha.listener.listener import Listener, ListenerSettings  # noqa: E402
from sasha.listener.playback import Player  # noqa: E402
from tests.listener_fakes import (  # noqa: E402
    FakeBackend,
    FakeIndicator,
    FakeVad,
    fake_player_command,
    scripted_mic_command,
)

UTTERANCE = [(30, 600), (3000, 1000), (30, 900)]  # silence, "speech", silence


class FakeBrain:
    """Records what the listener sends; `on_segment` decides the reply."""

    def __init__(self) -> None:
        self.received: list[Any] = []
        self.segments = 0
        self.ws: Any = None
        self.on_segment: Callable[[Any], Awaitable[None]] | None = None
        self.close_code: int | None = None
        self.connections = 0

    async def handler(self, ws: Any) -> None:
        self.connections += 1
        if self.close_code is not None:
            await ws.send(json.dumps({"type": "error", "message": "Too many connections"}))
            await ws.close(code=self.close_code, reason="slot taken")
            return
        self.ws = ws
        await ws.send(json.dumps({"type": "state", "state": "idle"}))  # as the real server
        async for msg in ws:
            self.received.append(msg if isinstance(msg, bytes) else json.loads(msg))
            if isinstance(msg, str) and json.loads(msg)["type"] == "audio_stop":
                self.segments += 1
                if self.on_segment:
                    await self.on_segment(ws)

    def json_types(self) -> list[str]:
        return [m["type"] for m in self.received if isinstance(m, dict)]


class Rig:
    def __init__(self, healthy: bool = True, pattern: list[tuple[int, int]] | None = None):
        self.brain = FakeBrain()
        self.backend = FakeBackend()
        self.indicator = FakeIndicator()
        self.healthy = healthy
        self.pattern = pattern or UTTERANCE
        self.captures: list[CaptureProcess] = []
        self.listener: Listener | None = None

    def capture(self) -> CaptureProcess:
        proc = CaptureProcess(scripted_mic_command(self.pattern))
        self.captures.append(proc)
        return proc

    async def health(self) -> BrainHealth:
        return BrainHealth(self.healthy, "" if self.healthy else "brain not reachable")

    @property
    def mic_open(self) -> bool:
        return bool(self.captures) and self.captures[-1].running

    async def run(self, body: Callable[[Rig, Listener], Awaitable[None]]) -> None:
        async with websockets.serve(self.brain.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            listener = Listener(
                ListenerSettings(
                    ws_url=f"ws://127.0.0.1:{port}/ws?wake=1&speak=1",
                    check_s=0.05,
                    reconnect_s=(0.05,),
                ),
                indicator=self.indicator,
                guards_factory=lambda cb: Guards(self.backend, cb, poll_s=0.05),
                gate=Gate(FakeVad()),
                capture_factory=self.capture,
                player=Player(fake_player_command(0.1)),
                health=self.health,
                connect=lambda url: websockets.connect(url),
            )
            self.listener = listener
            task = asyncio.create_task(listener.run())
            try:
                await body(self, listener)
            finally:
                listener.stop()
                await asyncio.wait_for(task, 5)
        assert not any(p.running for p in self.captures), "a capture child outlived the run"


async def until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def go(body: Callable[[Rig, Listener], Awaitable[None]], **kw: Any) -> Rig:
    rig = Rig(**kw)
    asyncio.run(rig.run(body))
    return rig


def test_segment_is_sent_and_reply_is_half_duplex() -> None:
    async def reply(ws: Any) -> None:
        await ws.send(json.dumps({"type": "wake", "heard": True, "command": True}))
        await ws.send(json.dumps({"type": "state", "state": "thinking"}))
        await ws.send(json.dumps({"type": "tts_start", "sample_rate": 24000}))
        await ws.send(b"\x01\x00" * 480)
        await ws.send(json.dumps({"type": "tts_end"}))
        await ws.send(json.dumps({"type": "state", "state": "idle"}))

    async def body(rig: Rig, lst: Listener) -> None:
        rig.brain.on_segment = reply
        await until(lambda: rig.brain.segments == 1)
        assert rig.brain.received[0] == {"type": "listener_state", "paused": False}
        start = next(
            m for m in rig.brain.received if isinstance(m, dict) and m["type"] == "audio_start"
        )
        assert start == {"type": "audio_start", "sample_rate": 16000, "wake": True}
        frames = [m for m in rig.brain.received if isinstance(m, bytes)]
        assert frames and all(len(f) == 640 for f in frames)
        await until(lambda: lst.playing)
        first = rig.captures[0]
        await until(lambda: not first.running, 1.0)  # mic closed while the reply plays
        assert "speaking" in rig.indicator.states
        await until(lambda: not lst.playing)
        await until(lambda: len(rig.captures) >= 2 and rig.mic_open)  # reopened after

    rig = go(body)
    assert rig.brain.json_types().count("audio_start") >= 1


def test_pause_from_the_indicator_thread_kills_the_child() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: rig.mic_open)
        proc = rig.captures[-1]
        assert rig.indicator.on_pause_toggle is not None
        threading.Thread(target=rig.indicator.on_pause_toggle).start()
        await until(lambda: not proc.running, 1.0)
        await until(lambda: {"type": "listener_state", "paused": True} in rig.brain.received)
        await asyncio.sleep(0.2)
        assert not rig.mic_open and rig.indicator.states[-1] == "paused"
        threading.Thread(target=rig.indicator.on_pause_toggle).start()
        await until(lambda: rig.mic_open)

    go(body)


def test_indicator_lost_closes_the_mic_at_once() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: rig.mic_open)
        proc = rig.captures[-1]
        threading.Thread(target=rig.indicator.lose).start()
        await until(lambda: not proc.running, 0.5)
        await asyncio.sleep(0.2)
        assert not rig.mic_open  # stays closed while available() is False

    go(body)


@pytest.mark.parametrize(
    ("kind", "value"), [("lock", True), ("active", False), ("battery", True), ("sleep", True)]
)
def test_each_guard_kills_the_child(kind: str, value: bool) -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: rig.mic_open)
        proc = rig.captures[-1]
        attr = {"lock": "locked", "active": "active", "battery": "battery"}.get(kind)
        if attr:
            setattr(rig.backend, attr, value)
        rig.backend.events_q.put_nowait(GuardEvent(kind, value))
        await until(lambda: not proc.running, 0.5)
        await asyncio.sleep(0.2)
        assert not rig.mic_open

    go(body)


def test_pause_capture_from_the_brain() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: rig.mic_open and rig.brain.ws is not None)
        await rig.brain.ws.send(json.dumps({"type": "pause_capture", "reason": "speaking"}))
        await until(lambda: not rig.mic_open, 1.0)
        await rig.brain.ws.send(json.dumps({"type": "resume_capture"}))
        await until(lambda: rig.mic_open)

    go(body)


def test_brain_offline_means_no_capture_then_reconnect() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await asyncio.sleep(0.3)
        assert rig.captures == [] and rig.indicator.states[-1] == "offline"
        rig.healthy = True
        await until(lambda: rig.mic_open)
        await rig.brain.ws.close()
        await until(lambda: not lst.online)
        assert not rig.mic_open
        await until(lambda: rig.brain.connections >= 2 and rig.mic_open)

    go(body, healthy=False)


def test_slot_taken_1013_is_final() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await until(lambda: lst.final_error is not None)
        await asyncio.sleep(0.3)
        assert rig.brain.connections == 1 and rig.captures == []

    rig = Rig()
    rig.brain.close_code = 1013
    asyncio.run(rig.run(body))


def test_tray_missing_waits_without_capture_or_exit() -> None:
    async def body(rig: Rig, lst: Listener) -> None:
        await asyncio.sleep(0.4)
        assert rig.captures == [] and not lst._stop.is_set()

    rig = Rig()
    rig.indicator = FakeIndicator(available=False)
    asyncio.run(rig.run(body))


def test_no_files_are_written(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import pathlib
    import tempfile

    writes: list[str] = []
    real_open = builtins.open

    def guarded_open(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        if any(c in mode for c in "wax+"):
            writes.append(f"open {file} {mode}")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)
    for name in ("write_text", "write_bytes"):
        monkeypatch.setattr(pathlib.Path, name, lambda self, *a, **k: writes.append(str(self)))
    for name in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile"):
        monkeypatch.setattr(tempfile, name, lambda *a, _n=name, **k: writes.append(_n))

    async def reply(ws: Any) -> None:
        await ws.send(json.dumps({"type": "wake", "heard": False, "command": False}))

    async def body(rig: Rig, lst: Listener) -> None:
        rig.brain.on_segment = reply
        await until(lambda: rig.brain.segments == 1)

    go(body)
    assert writes == [], writes
    assert os.environ.get("SASHA_DEBUG_DUMP") is None


@pytest.mark.parametrize(
    ("heard", "command", "earcons"),
    [(True, True, ["wake"]), (True, False, ["armed"]), (False, False, [])],
)
def test_earcons_follow_the_wake_answer(heard: bool, command: bool, earcons: list[str]) -> None:
    async def reply(ws: Any) -> None:
        msg = {"type": "wake", "heard": heard, "command": command}
        if heard and not command:
            msg["armed_s"] = 5
        await ws.send(json.dumps(msg))
        await ws.send(json.dumps({"type": "state", "state": "idle"}))

    async def body(rig: Rig, lst: Listener) -> None:
        rig.brain.on_segment = reply
        await until(lambda: rig.brain.segments == 1)
        await asyncio.sleep(0.3)
        assert rig.indicator.earcons == earcons
        assert not lst.busy  # back to listening after the idle state

    go(body)
