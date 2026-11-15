"""The listener socket (`/ws?wake=1`): hits, misses, the armed window, drops, limits."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from sasha.config import Settings
from sasha.orchestrator import Assistant
from sasha.pipeline.tts import SpeechAudio
from sasha.telemetry import RequestTrace, TelemetryWriter
from tests.server_fakes import WS, FakeTTS, resp
from tests.wake_fakes import (
    SENTINEL,
    frames,
    listen,
    make,
    segment,
    telemetry_lines,
    text_of,
    turn_frames,
)

LISTENER = f"{WS}/ws?wake=1"


class SlowTTS(FakeTTS):
    """Speaks its first chunk at once, then waits for `release` before finishing."""

    def stream(self, text: str, language: str) -> Iterator[SpeechAudio]:
        self.spoken.append((text, language))
        yield SpeechAudio(b"\x01\x00" * 720, self.sample_rate)
        self.release.wait(5)
        yield SpeechAudio(b"\x01\x00" * 720, self.sample_rate)


@pytest.fixture
def name(settings: Settings) -> str:
    return settings.assistant.wake_word.phrase


def eventually(check: Callable[[], bool], timeout: float = 2.0) -> None:
    """The server handles a frame on its own thread and some frames get no reply, so a
    check made straight after `send_json` (or after the socket closes) can run first.
    Poll briefly; the assertion itself is unchanged."""
    end = time.monotonic() + timeout
    while not check() and time.monotonic() < end:
        time.sleep(0.01)
    assert check()


def test_name_and_command_runs_a_turn(settings: Settings, tmp_path: Path, name: str) -> None:
    h, stt = make(settings, tmp_path)
    stt.say(f"{name}, tell me a joke")  # not a device request: the reply streams as is
    h.llm("big-model").responses = [resp("Here you go")]
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        got = turn_frames(ws)
    assert got[0] == {"type": "wake", "heard": True, "command": True}
    assert {"type": "transcript", "text": "tell me a joke", "final": True} in got
    assert any(f.get("type") == "reply" and f["text"] == "Here you go" for f in got)
    user = [m["content"] for m in h.llm("big-model").seen[0][0] if m["role"] == "user"]
    assert user == ["tell me a joke"]  # the name itself never reaches the LLM


def test_name_alone_then_command(settings: Settings, tmp_path: Path, name: str) -> None:
    h, stt = make(settings, tmp_path)
    stt.say(name, "lights off please")
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        assert ws.receive_json() == {
            "type": "wake",
            "heard": True,
            "command": False,
            "armed_s": 5.0,
        }
        segment(ws)
        got = turn_frames(ws)
    assert got[0] == {"type": "wake", "heard": True, "command": True}
    user = [m["content"] for m in h.llm("big-model").seen[0][0] if m["role"] == "user"]
    assert user == ["lights off please"]


def test_armed_window_expires_and_drops(settings: Settings, tmp_path: Path, name: str) -> None:
    h, stt = make(settings, tmp_path, armed_window_s=0.2)
    stt.say(name, "lights off please")
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        assert ws.receive_json()["armed_s"] == 0.2
        ws.send_json({"type": "audio_start", "sample_rate": 16000, "wake": True})
        ws.send_bytes(b"\x00\x00" * 1600)  # a segment is still arriving when it expires
        assert ws.receive_json() == {"type": "armed_expired"}
        ws.send_json({"type": "audio_stop"})  # buffer was cleared: nothing to check
        segment(ws)  # fresh segment, no name: a miss
        assert ws.receive_json() == {"type": "wake", "heard": False, "command": False}
    assert stt.calls == 2  # name, then the fresh segment; the cut one never transcribed
    assert h.llm("big-model").seen == []
    outcomes = [r["outcome"] for r in telemetry_lines(h) if r.get("kind") == "wake_check"]
    assert outcomes == ["accepted_alone", "followup_expired", "rejected_no_name"]


def test_miss_is_dropped_structurally(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D4: a miss starts no trace, never calls record_text (even with log_text on),
    never reaches the LLM, and its text appears in no frame, log or telemetry."""
    calls: list[str] = []
    real_start, real_record = TelemetryWriter.start, RequestTrace.record_text
    real_handle = Assistant.handle_text
    monkeypatch.setattr(
        TelemetryWriter, "start", lambda s, *a, **k: calls.append("start") or real_start(s, *a, **k)
    )
    monkeypatch.setattr(
        RequestTrace, "record_text", lambda s, *a: calls.append("record_text") or real_record(s, *a)
    )
    monkeypatch.setattr(
        Assistant,
        "handle_text",
        lambda s, *a, **k: calls.append("handle") or real_handle(s, *a, **k),
    )
    caplog.set_level("DEBUG")
    h, stt = make(settings, tmp_path, log_text=True)
    stt.say(f"{SENTINEL} and then I told her", f"something {SENTINEL} else entirely")
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        first = [ws.receive_json()]
        segment(ws)
        first.append(ws.receive_json())
        ws.send_json({"type": "audio_stop"})  # no open segment: the buffer is empty
        ws.send_json({"type": "cancel"})
        ws.send_json({"type": "listener_stats", "gate_drops": 3})
    assert first == [{"type": "wake", "heard": False, "command": False}] * 2
    assert calls == [] and h.llm("big-model").seen == [] and stt.calls == 2
    assert SENTINEL not in caplog.text
    eventually(lambda: any(r["kind"] == "wake_gate" for r in telemetry_lines(h)))
    lines = list(telemetry_lines(h))
    assert SENTINEL not in str(lines) and SENTINEL not in text_of(first)
    outcomes = [r["outcome"] for r in lines if r["kind"] == "wake_check"]
    assert outcomes == ["rejected_no_name", "rejected_no_name"]
    assert {"kind": "wake_gate", "gate_drops": 3}.items() <= next(
        r for r in lines if r["kind"] == "wake_gate"
    ).items()


def test_hit_with_log_text_records_only_the_command(
    settings: Settings, tmp_path: Path, name: str
) -> None:
    h, stt = make(settings, tmp_path, log_text=True)
    stt.say(f"{name}, what time is it")
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        turn_frames(ws)
    turns = [r for r in telemetry_lines(h) if "stages" in r]
    assert turns and turns[0]["input_mode"] == "wake"
    assert turns[0]["text"]["user"] == "what time is it"  # the owner's log_text choice


def test_cooldown_drops_before_transcribing(settings: Settings, tmp_path: Path, name: str) -> None:
    h, stt = make(settings, tmp_path, cooldown_s=30.0)
    stt.say(f"{name}, lights on", f"{name}, lights off")
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        turn_frames(ws)
        segment(ws)
        assert ws.receive_json() == {"type": "wake", "heard": False, "command": False}
    assert stt.calls == 1  # the second segment was never transcribed


def test_busy_drops_before_transcribing(settings: Settings, tmp_path: Path, name: str) -> None:
    h, stt = make(settings, tmp_path)
    stt.say(f"{name}, tell me a story", f"{name}, stop")
    llm = h.llm("big-model")
    llm.hold = True
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        assert ws.receive_json()["heard"] is True
        assert llm.started.wait(5)
        segment(ws)
        dropped = frames(ws, "wake")
        llm.hold = False
        turn_frames(ws)
    assert dropped[-1] == {"type": "wake", "heard": False, "command": False}
    assert stt.calls == 1


def test_paused_listener_gets_no_wake_checks(settings: Settings, tmp_path: Path, name: str) -> None:
    h, stt = make(settings, tmp_path)
    stt.say(f"{name}, lights on")
    with h.client().websocket_connect(LISTENER) as ws:
        assert ws.receive_json()["state"] == "idle"
        segment(ws)  # no listener_state yet: fail closed, treated as paused
        assert ws.receive_json() == {"type": "error", "message": "Listener is paused"}
        ws.send_json({"type": "listener_state", "paused": False})
        eventually(lambda: h.deps.presence.active is True)
        ws.send_json({"type": "listener_state", "paused": True})
        segment(ws)
        assert ws.receive_json()["message"] == "Listener is paused"
        assert h.deps.presence.active is False
        ws.send_json({"type": "listener_state", "paused": False})
        eventually(lambda: h.deps.presence.keep_alive() == -1)
    eventually(lambda: h.deps.presence.active is False)  # disconnect clears it
    assert stt.calls == 0


def test_listener_refuses_text_bad_bot_and_non_wake_audio(
    settings: Settings, tmp_path: Path
) -> None:
    h, _ = make(settings, tmp_path)
    client = h.client()
    with client.websocket_connect(LISTENER) as ws:
        listen(ws)
        ws.send_json({"type": "text", "text": "hello"})
        assert ws.receive_json()["message"] == "This socket takes speech only"
        ws.send_json({"type": "audio_start", "sample_rate": 16000})
        assert "wake: true" in ws.receive_json()["message"]
    with client.websocket_connect(f"{LISTENER}&bot=main") as ws:
        assert "voice bot" in ws.receive_json()["message"]
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1008


def test_segment_cap(settings: Settings, tmp_path: Path) -> None:
    h, stt = make(settings, tmp_path, max_segment_s=0.1)
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws, b"\x00\x00" * 4000)  # 0.25 s
        assert ws.receive_json()["message"] == "Segment too long; dropped"
    assert stt.calls == 0


def test_reserved_slot_for_one_listener(settings: Settings, tmp_path: Path) -> None:
    h, _ = make(settings, tmp_path)
    client = h.client()
    tabs = [client.websocket_connect(f"{WS}/ws") for _ in range(4)]  # the UI's full budget
    opened = [t.__enter__() for t in tabs]
    try:
        for ws in opened:
            assert ws.receive_json()["state"] == "idle"
        with client.websocket_connect(LISTENER) as listener:
            listen(listener)  # still accepted: the reserved slot
            with client.websocket_connect(LISTENER) as second:
                assert "already connected" in second.receive_json()["message"]
                with pytest.raises(WebSocketDisconnect) as closed:
                    second.receive_json()
                assert closed.value.code == 1013
    finally:
        for t in tabs:
            t.__exit__(None, None, None)


def test_speaking_anywhere_pauses_the_listener(
    settings: Settings, tmp_path: Path, name: str
) -> None:
    """D2: the Home socket speaking closes the listener's mic and drops its segments."""
    tts = SlowTTS()
    h, stt = make(settings, tmp_path, tts=tts, speaking_tail_s=0.05)
    stt.say(f"{name}, lights on")
    client = h.client()
    with (
        client.websocket_connect(LISTENER) as listener,
        client.websocket_connect(f"{WS}/ws?speak=1") as home,
    ):
        listen(listener)
        assert home.receive_json()["state"] == "idle"
        home.send_json({"type": "text", "text": "tell me something"})
        assert listener.receive_json() == {"type": "pause_capture", "reason": "speaking"}
        segment(listener)
        assert listener.receive_json() == {"type": "wake", "heard": False, "command": False}
        tts.release.set()
        turn_frames(home)
        assert listener.receive_json() == {"type": "resume_capture"}
    assert stt.calls == 0
    outcomes = [r["outcome"] for r in telemetry_lines(h) if r.get("kind") == "wake_check"]
    assert outcomes == ["dropped_speaking"]


def test_telemetry_has_labels_and_timings_only(
    settings: Settings, tmp_path: Path, name: str
) -> None:
    h, stt = make(settings, tmp_path)
    stt.say(f"{name}, lights on")
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        turn_frames(ws)
    check = next(r for r in telemetry_lines(h) if r.get("kind") == "wake_check")
    assert check["outcome"] == "accepted_with_command" and check["variant"] == 0
    assert check["segment_ms"] == 200 and check["stt_ms"] >= 0
    assert set(check) <= {
        "schema",
        "started_at",
        "profile",
        "kind",
        "outcome",
        "segment_ms",
        "stt_ms",
        "variant",
    }


def test_window_turn_still_works_with_a_listener_connected(
    settings: Settings, tmp_path: Path
) -> None:
    h, _ = make(settings, tmp_path)
    client = h.client()
    with (
        client.websocket_connect(LISTENER) as listener,
        client.websocket_connect(f"{WS}/ws") as tab,
    ):
        listen(listener)
        assert tab.receive_json()["state"] == "idle"
        tab.send_json({"type": "text", "text": "hi"})
        assert any(f.get("type") == "reply" for f in turn_frames(tab))
        time.sleep(0.01)
