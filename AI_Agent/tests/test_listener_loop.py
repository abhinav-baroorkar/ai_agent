"""The listener main loop against a fake brain (a real local WebSocket server)."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest

np = pytest.importorskip("numpy")
websockets = pytest.importorskip("websockets")

from sasha.listener.guards import GuardEvent  # noqa: E402
from sasha.listener.listener import Listener  # noqa: E402
from tests.listener_fakes import FakeIndicator  # noqa: E402
from tests.listener_rig import Rig, go, until  # noqa: E402


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
        # (after a turn the mic reopens and the fake mic replays its utterance, so the
        # same answer can come again: each answer gives its own earcon, or none)
        assert rig.indicator.earcons[:1] == earcons and set(rig.indicator.earcons) <= set(earcons)

    go(body)
