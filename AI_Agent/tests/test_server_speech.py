"""Speech on `speak=1` sockets: spoken while streaming, language check, cancel."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from sasha.config import Settings
from sasha.server.voice import basic_split_sentences, sentence_splitter, tts_languages
from tests.server_fakes import WS, FakeSTT, FakeTTS, Harness, resp
from tests.test_server_ws import frames_until_idle, opened, types


def _harness(settings: Settings, tmp_path: Path, tts: FakeTTS) -> Harness:
    return Harness(settings, tmp_path, stt=FakeSTT(), tts=tts)


def test_typed_text_is_spoken_on_speak_1(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    h = _harness(settings, tmp_path, tts)
    h.llm("big-model").responses = [resp("The light is on. The fan is off. Done")]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "status?"})
        frames = frames_until_idle(ws, after_first=False)
    # Sentences go to TTS as they complete; the tail is flushed at the end.
    assert tts.spoken == [("The light is on.", "en"), ("The fan is off.", "en"), ("Done", "en")]
    t = types(frames)
    assert t.count("tts_start") == 1 and t.count("tts_end") == 1
    assert t.index("tts_start") < t.index("tts_end") < len(t) - 1
    assert b"".join(f for f in frames if isinstance(f, bytes)) == b"\x01\x00" * 1440 * 3
    assert {"type": "state", "state": "speaking"} in frames
    assert frames[-1] == {"type": "state", "state": "idle"}


def test_language_not_spoken_sends_text_only(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS(languages=["en"])
    h = Harness(settings, tmp_path, stt=FakeSTT(language="mr"), tts=tts)
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        opened(ws)
        ws.send_json({"type": "audio_start", "sample_rate": 16000})
        ws.receive_json()
        ws.send_bytes(b"\x00\x00" * 320)
        ws.send_json({"type": "audio_stop"})
        frames = frames_until_idle(ws, after_first=False)
    assert "reply" in types(frames) and "tts_start" not in types(frames)
    assert tts.spoken == []


def test_cancel_stops_speech_in_flight(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    tts.hold.set()  # each chunk waits for `release`
    h = _harness(settings, tmp_path, tts)
    h.llm("big-model").responses = [resp("One sentence here. Two sentence here. Three")]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "talk"})
        while ws.receive_json()["type"] != "reply":
            pass
        ws.send_json({"type": "cancel"})
        tts.release.set()
        frames = frames_until_idle(ws, after_first=False)
    assert len(tts.spoken) == 1  # the queued sentences were dropped
    assert frames[-1] == {"type": "state", "state": "idle"}
    audio = [f for f in frames if isinstance(f, bytes)]
    assert len(b"".join(audio)) <= 1440  # at most the one chunk that was in flight


def test_bad_speak_value_closes_with_reason(settings: Settings, tmp_path: Path) -> None:
    h = _harness(settings, tmp_path, FakeTTS())
    with h.client().websocket_connect(f"{WS}/ws?speak=yes") as ws:
        assert ws.receive_json() == {"type": "error", "message": "speak must be 0 or 1"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1008 and closed.value.reason == "speak must be 0 or 1"


def test_each_socket_has_its_own_history(settings: Settings, tmp_path: Path) -> None:
    h = _harness(settings, tmp_path, FakeTTS())
    client = h.client()
    with (
        client.websocket_connect(f"{WS}/ws") as a,
        client.websocket_connect(f"{WS}/ws") as b,
    ):
        opened(a)
        opened(b)
        a.send_json({"type": "text", "text": "from a"})
        frames_until_idle(a, after_first=False)
        b.send_json({"type": "text", "text": "from b"})
        frames_until_idle(b, after_first=False)
    last_messages = h.llm("big-model").seen[-1][0]
    assert [m["content"] for m in last_messages if m["role"] == "user"] == ["from b"]


def test_splitter_and_languages_helpers() -> None:
    assert basic_split_sentences("Hi there. How are you? Fine") == [
        "Hi there.",
        "How are you?",
        "Fine",
    ]
    assert callable(sentence_splitter())
    assert tts_languages(None) == [] and tts_languages(FakeTTS(["en", "mr"])) == ["en", "mr"]
