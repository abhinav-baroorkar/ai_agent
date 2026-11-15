"""Spoken confirmation with speech on (speak=1), and the playback-based window (S3)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sasha.config import Settings
from sasha.pipeline.llm import ToolCall
from sasha.pipeline.tts import SpeechAudio
from sasha.server.wake_confirm import PlaybackClock
from tests.server_fakes import WS, FakeTTS, resp
from tests.wake_fakes import frames, listen, make, segment, turn_frames, with_devices

OFF_ALL = ToolCall("call_service", {"domain": "light", "service": "turn_off", "entity_id": "all"})


class QuestionHoldTTS(FakeTTS):
    """Speaks normally, but holds the confirmation question until `release`."""

    def stream(self, text: str, language: str) -> Iterator[SpeechAudio]:
        self.spoken.append((text, language))
        yield SpeechAudio(b"\x01\x00" * 720, self.sample_rate)
        if text.startswith("Should I"):
            self.release.wait(5)
        yield SpeechAudio(b"\x01\x00" * 720, self.sample_rate)


def _types(items: list[Any]) -> list[str]:
    return [f["type"] if isinstance(f, dict) else "<audio>" for f in items]


def test_w1_confirm_comes_after_the_earlier_speech_ends(settings: Settings, tmp_path: Path) -> None:
    """Speech before the tool call ends (tts_end) before `confirm`; then the question."""
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path, tts=FakeTTS(), speaking_tail_s=0.05)
    with_devices(h)
    # Not worded as a device request, so the model's sentence before its tool call is
    # spoken (a change request would hold it, T1); the model still acts on it.
    stt.say(f"{name}, it's too bright in here", "yes")
    before = resp("Let me check that for you.", [OFF_ALL])
    h.llm("big-model").responses = [before, resp("Done.")]
    with h.client().websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        segment(ws)
        before_confirm = _types(frames(ws, "confirm"))
        question = _types(frames(ws, "tts_end"))  # the question's own utterance
        segment(ws)
        turn_frames(ws)
    # The earlier speech started and ended before `confirm` ...
    assert before_confirm.index("tts_start") < before_confirm.index("tts_end")
    # ... so the listener's next tts_end after `confirm` is the question's.
    assert question.count("tts_start") == 1 and question.count("tts_end") == 1
    assert question.index("tts_start") < question.index("tts_end")


def test_w2_cancel_during_the_question_ends_at_once(settings: Settings, tmp_path: Path) -> None:
    name = settings.assistant.wake_word.phrase
    tts = QuestionHoldTTS()
    h, stt = make(settings, tmp_path, tts=tts, speaking_tail_s=0.05)
    dev = with_devices(h)
    stt.say(f"{name}, turn off all the lights")
    h.llm("big-model").responses = [resp("", [OFF_ALL]), resp("Okay.")]
    with h.client().websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        segment(ws)
        frames(ws, "confirm")
        frames(ws, "tts_start")  # the question is being spoken (and held)
        segment(ws)  # a segment before the question ended: not an answer
        early = frames(ws, "wake")
        started = time.perf_counter()
        ws.send_json({"type": "cancel"})
        rest = turn_frames(ws)
        elapsed = time.perf_counter() - started
        tts.release.set()  # let the held speech thread finish before the socket closes
    assert early[-1] == {"type": "wake", "heard": False, "command": False}
    assert stt.calls == 1  # the early segment was never transcribed
    assert {"type": "confirm_result", "accepted": False, "reason": "no"} in rest
    assert dev.calls == [] and elapsed < 3  # no waiting out the 5 s window


class Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


def test_window_starts_at_estimated_end_of_playback() -> None:
    """Frames sent all at once still play for their full duration."""
    clock = Clock()
    pb = PlaybackClock(clock)
    pb.tts_start(24000)
    for _ in range(50):
        pb.frame(960)  # 20 ms each at 24 kHz: 1.0 s of audio, sent instantly
    assert pb.remaining(0.3) == pytest.approx(1.3)
    clock.now += 0.5
    assert pb.remaining(0.3) == pytest.approx(0.8)


def test_slow_frames_push_the_end_out() -> None:
    clock = Clock()
    pb = PlaybackClock(clock)
    pb.tts_start(16000)
    pb.frame(640)  # 20 ms
    clock.now += 2.0  # synthesis stalled: the next frame arrives late
    pb.frame(640)
    assert pb.remaining(0.0) == pytest.approx(0.02)


def test_confirm_with_speech_asks_aloud_then_waits_for_playback(
    settings: Settings, tmp_path: Path
) -> None:
    """The question is its own utterance (tts_start..tts_end); the turn then goes on."""
    name = settings.assistant.wake_word.phrase
    tts = FakeTTS()
    h, stt = make(settings, tmp_path, tts=tts, speaking_tail_s=0.05)
    dev = with_devices(h)
    stt.say(f"{name}, turn off all the lights", "yes")
    h.llm("big-model").responses = [resp("", [OFF_ALL]), resp("All off now.")]
    started = time.perf_counter()
    with h.client().websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        segment(ws)
        asked = frames(ws, "confirm")
        question = frames(ws, "tts_end")  # the question was spoken, start to end
        segment(ws)  # the listener's one-segment window
        rest = turn_frames(ws)
    elapsed = time.perf_counter() - started
    assert asked[-1]["type"] == "confirm"
    assert any(isinstance(f, dict) and f.get("type") == "tts_start" for f in question)
    assert ("Should I turn off all light devices?", "en") in tts.spoken
    assert {"type": "confirm_result", "accepted": True, "reason": "yes"} in rest
    assert dev.calls == [{"domain": "light", "service": "turn_off", "entity_id": "all"}]
    assert ("All off now.", "en") in tts.spoken  # the rest of the turn is spoken too
    assert elapsed < 5  # no 30 s wait for a tts_end that only comes at turn end
