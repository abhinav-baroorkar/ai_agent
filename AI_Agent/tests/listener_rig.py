"""A fake brain (a real local WebSocket server) and a rig around the real Listener."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from sasha.listener.brain import BrainHealth
from sasha.listener.capture import CaptureProcess
from sasha.listener.contracts import ListenerSettings
from sasha.listener.gate import Gate
from sasha.listener.guards import Guards
from sasha.listener.listener import Listener
from sasha.listener.playback import Player
from tests.listener_fakes import (
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
