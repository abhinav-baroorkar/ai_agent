from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from sasha.bots import SAFETY_NOTE
from sasha.config import Settings
from sasha.pipeline.llm import ToolCall
from tests.server_fakes import OWN_ORIGIN, WS, FakeSTT, FakeTTS, Harness, resp


def frames_until_idle(ws: WebSocketTestSession, after_first: bool = True) -> list[Any]:
    """Collect frames until the server says `idle` (skipping the opening idle)."""
    out: list[Any] = []
    while True:
        msg = ws.receive()
        if msg.get("bytes") is not None:
            out.append(msg["bytes"])
            continue
        frame = json.loads(msg["text"])
        out.append(frame)
        if frame == {"type": "state", "state": "idle"} and (len(out) > 1 or not after_first):
            return out


def opened(ws: WebSocketTestSession) -> None:
    assert ws.receive_json() == {"type": "state", "state": "idle"}


def types(frames: list[Any]) -> list[str]:
    return ["<audio>" if isinstance(f, bytes) else f["type"] for f in frames]


@pytest.fixture
def h(settings: Settings, tmp_path: Path) -> Harness:
    return Harness(settings, tmp_path)


def test_text_turn_streams_then_replies(h: Harness) -> None:
    h.llm("big-model").responses = [resp("Hi there friend")]
    with h.client().websocket_connect(f"{WS}/ws?bot=main") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "hello"})
        frames = frames_until_idle(ws, after_first=False)
    assert frames[0] == {"type": "state", "state": "thinking"}
    deltas = [f["text"] for f in frames if f["type"] == "reply_delta"]
    assert "".join(deltas) == "Hi there friend" and len(deltas) == 3
    reply = next(f for f in frames if f["type"] == "reply")
    assert reply["text"] == "Hi there friend" and reply["tool_calls"] == []
    assert reply["timing"]["tokens_per_s"] == 100.0 and reply["timing"]["total_ms"] >= 0
    assert frames[-1] == {"type": "state", "state": "idle"}


def test_tool_frame_and_untrusted_wrapping(h: Harness) -> None:
    llm = h.llm("big-model")
    llm.responses = [resp("", [ToolCall("fake_echo", {"text": "ignore previous"})]), resp("Done")]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "echo something"})
        frames = frames_until_idle(ws, after_first=False)
    assert {"type": "tool", "name": "fake_echo", "ok": True} in frames
    reply = next(f for f in frames if f["type"] == "reply")
    assert reply["tool_calls"] == [{"name": "fake_echo", "ok": True}]
    tool_msg = llm.seen[1][0][-1]
    assert tool_msg["role"] == "tool"
    assert json.loads(tool_msg["content"])["untrusted_data"]["echo"] == "ignore previous"


def test_bot_gets_only_its_connectors_tools(h: Harness) -> None:
    client = h.client()
    bot = client.post(
        "/api/bots", json={"name": "Plain", "model": "small", "system_prompt": "Be brief."}
    )
    bot_id = bot.json()["id"]
    with client.websocket_connect(f"{WS}/ws?bot={bot_id}") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "hi"})
        frames_until_idle(ws, after_first=False)
    messages, offered_tools = h.llm("small").seen[0]
    assert offered_tools is False  # no connectors -> no tools
    assert messages[0]["content"].startswith("Be brief.")
    assert SAFETY_NOTE in messages[0]["content"]


def test_disabled_connector_gives_no_tools(h: Harness) -> None:
    client = h.client()
    client.put("/api/connectors/fake", json={"enabled": False})
    with client.websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "hi"})
        frames_until_idle(ws, after_first=False)
    assert h.llm("big-model").seen[0][1] is False


def test_unknown_bot_gets_error_and_close(h: Harness) -> None:
    with h.client().websocket_connect(f"{WS}/ws?bot=nope") as ws:
        assert ws.receive_json() == {"type": "error", "message": "Unknown bot 'nope'"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_cross_site_origin_rejected(h: Harness) -> None:
    client = h.client()
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(f"{WS}/ws", headers={"origin": "http://evil.example"}) as ws,
    ):
        ws.receive_json()
    with client.websocket_connect(f"{WS}/ws", headers={"origin": OWN_ORIGIN}) as ws:
        opened(ws)


def test_cancel_stops_reply_and_reset_clears_history(h: Harness) -> None:
    llm = h.llm("big-model")
    llm.hold = True
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "tell me a long story"})
        assert ws.receive_json() == {"type": "state", "state": "thinking"}
        assert llm.started.wait(5)
        ws.send_json({"type": "text", "text": "again"})
        assert ws.receive_json()["message"].startswith("Busy")
        ws.send_json({"type": "cancel"})
        assert ws.receive_json() == {"type": "state", "state": "idle"}  # no reply, no error

        llm.hold = False
        ws.send_json({"type": "text", "text": "first"})
        frames_until_idle(ws, after_first=False)
        ws.send_json({"type": "reset"})
        assert ws.receive_json() == {"type": "state", "state": "idle"}
        ws.send_json({"type": "text", "text": "second"})
        frames_until_idle(ws, after_first=False)
    history = [m["content"] for m in llm.seen[-1][0] if m["role"] == "user"]
    assert history == ["second"]


def test_bad_frames_get_errors(h: Harness) -> None:
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        for bad in ("not json", "[]", json.dumps({"type": "dance"}), json.dumps({"type": "text"})):
            ws.send_text(bad)
            assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "text", "text": "x" * 9000})
        assert "too long" in ws.receive_json()["message"]


def test_audio_without_voice_stages_gets_error(h: Harness) -> None:
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "audio_start", "sample_rate": 16000})
        assert "Voice is not available" in ws.receive_json()["message"]
        ws.send_bytes(b"\x00\x00" * 320)  # ignored
        ws.send_json({"type": "audio_stop"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "text", "text": "still works"})
        frames = frames_until_idle(ws, after_first=False)
    assert "reply" in types(frames) and "tts_start" not in types(frames)


def test_voice_turn_with_fake_stt_and_tts(settings: Settings, tmp_path: Path) -> None:
    stt, tts = FakeSTT(), FakeTTS()
    h = Harness(settings, tmp_path, stt=stt, tts=tts)
    h.llm("big-model").responses = [resp("Light is on")]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "audio_start", "sample_rate": 16000})
        assert ws.receive_json() == {"type": "state", "state": "listening"}
        for _ in range(3):
            ws.send_bytes(b"\x00\x01" * 320)  # 20 ms at 16 kHz
        ws.send_json({"type": "audio_stop"})
        frames = frames_until_idle(ws, after_first=False)
    assert stt.heard == [b"\x00\x01" * 960]
    assert {"type": "transcript", "text": "turn on the light", "final": True} in frames
    t = types(frames)
    assert t.index("transcript") < t.index("reply") < t.index("tts_start") < t.index("tts_end")
    assert {"type": "state", "state": "speaking"} in frames
    assert next(f for f in frames if t[frames.index(f)] == "tts_start")["sample_rate"] == 24000
    audio = [f for f in frames if isinstance(f, bytes)]
    assert b"".join(audio) == b"\x01\x00" * 1440
    assert [len(a) for a in audio] == [960, 480, 960, 480]  # 20 ms frames per sentence
    assert tts.spoken == [("Light is on", "en")]
    assert frames[-1] == {"type": "state", "state": "idle"}


def test_typed_text_is_not_spoken(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    h = Harness(settings, tmp_path, stt=FakeSTT(), tts=tts)
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "hi"})
        frames = frames_until_idle(ws, after_first=False)
    assert "tts_start" not in types(frames) and tts.spoken == []


def test_audio_rejects_wrong_rate_and_cancel_discards(settings: Settings, tmp_path: Path) -> None:
    stt = FakeSTT()
    h = Harness(settings, tmp_path, stt=stt, tts=None)
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "audio_start", "sample_rate": 44100})
        assert "16000" in ws.receive_json()["message"]
        ws.send_json({"type": "audio_start", "sample_rate": 16000})
        assert ws.receive_json()["state"] == "listening"
        ws.send_bytes(b"\x00\x00" * 320)
        ws.send_json({"type": "cancel"})
        assert ws.receive_json() == {"type": "state", "state": "idle"}
        ws.send_json({"type": "audio_stop"})  # after cancel: ignored
        ws.send_json({"type": "text", "text": "hi"})
        frames = frames_until_idle(ws, after_first=False)
    assert stt.heard == [] and "reply" in types(frames)


def test_message_text_not_logged(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    h.llm("big-model").responses = [resp("secret answer")]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "my secret question"})
        frames_until_idle(ws, after_first=False)
    assert "secret" not in caplog.text
    lines = list((h.settings.paths.telemetry_dir).glob("*.jsonl"))
    assert lines and "secret" not in lines[0].read_text(encoding="utf-8")
