"""Follow-ups from the spoken-text and chat-behaviour reviews: the hold cap (SP1), a
linear link pattern (SP2), "2*3", and the history keeping what was really said."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from sasha.config import Settings
from sasha.orchestrator import Assistant
from sasha.server.spoken import MAX_HOLD, SpokenStream, clean_line, to_spoken
from sasha.telemetry import TelemetryWriter
from sasha.tools import ToolRegistry
from tests.server_fakes import WS, FakeLLM, FakeTTS, Harness, resp
from tests.test_chat_behaviour import StoppedLLM
from tests.wake_fakes import frames, listen, make, segment, turn_frames, with_devices

# --- SP1: speech can't stall behind a long held span ---------------------------------------


def test_a_long_unclosed_link_is_cut_at_the_cap() -> None:
    stream = SpokenStream()
    out = stream.feed("See [" + "word " * 100)  # no "]", no sentence end, 500 characters
    assert out and "[" not in out and len(stream.buffer) <= MAX_HOLD


def test_a_long_sentence_without_a_stop_still_streams() -> None:
    stream = SpokenStream()
    pieces = [stream.feed("and then " * 5) for _ in range(10)]  # 450 characters, no "."
    assert any(pieces) and len(stream.buffer) <= MAX_HOLD


def test_a_link_cut_apart_never_reads_its_address() -> None:
    assert clean_line("the docs](http://example.org/x) here", line_start=False) == "the docs here"


# --- SP2: no quadratic time on hostile input ---------------------------------------------------


@pytest.mark.parametrize("hostile", ["[" * 200_000, "[a](" * 50_000, "![" * 100_000 + "]("])
def test_link_pattern_is_linear(hostile: str) -> None:
    started = time.perf_counter()
    clean_line(hostile)
    to_spoken(hostile[:50_000])
    assert time.perf_counter() - started < 1.0


# --- "2*3" ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("2*3 is 6", "2 times 3 is 6"),
        ("so **4 * 5** = 20", "so 4 times 5 = 20"),
        ("a *word* stays a word", "a word stays a word"),
    ],
)
def test_multiplication_is_said(text: str, spoken: str) -> None:
    assert clean_line(text) == spoken


def test_the_times_word_comes_from_config() -> None:
    assert to_spoken("2*3", times="mal") == "2 mal 3"


# --- the history keeps what was shown and spoken -------------------------------------------


def history(llm: FakeLLM) -> list[tuple[str, str]]:
    return [(m["role"], str(m["content"])) for m in llm.seen[-1][0] if m["role"] != "system"]


def test_wake_turn_remembers_the_correction_not_the_claim(
    settings: Settings, tmp_path: Path
) -> None:
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path)
    with_devices(h)
    stt.say(f"{name}, lights off", f"{name}, did you do it?")
    h.llm("big-model").responses = [resp("The lights are off now."), resp("No.")]
    with h.client().websocket_connect(f"{WS}/ws?wake=1") as ws:
        listen(ws)
        segment(ws)
        turn_frames(ws)
        segment(ws)
        turn_frames(ws)
    remembered = history(h.llm("big-model"))[1][1]
    assert remembered == "Nothing was changed."  # what it said, not the false claim


def test_spoken_turn_remembers_the_speakable_text(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path, tts=FakeTTS())
    h.llm("big-model").responses = [resp("**Yes**, it is."), resp("ok")]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        ws.receive_json()
        for text in ("Is it?", "Thanks."):
            ws.send_json({"type": "text", "text": text})
            frames(ws, "state", "idle")
    assert history(h.llm("big-model"))[1] == ("assistant", "Yes, it is.")


def test_stopped_spoken_turn_keeps_only_what_was_released(
    settings: Settings, tmp_path: Path
) -> None:
    h = Harness(settings, tmp_path, tts=FakeTTS())
    llm = StoppedLLM()  # streams "Twinkle twinkle little" with no sentence end
    h.llms["big-model"] = llm
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "Recite a poem"})
        assert llm.streaming.wait(5)
        ws.send_json({"type": "cancel"})
        frames(ws, "state", "idle")
        ws.send_json({"type": "text", "text": "continue"})
        frames(ws, "state", "idle")
    note = settings.profile.llm.interrupted_note
    assert history(llm)[1] == ("assistant", note)  # nothing was spoken yet: just the note


def test_a_turn_not_in_history_replaces_nothing(settings: Settings, tmp_path: Path) -> None:
    llm = FakeLLM("m")
    llm.responses = [resp("first")]
    a = Assistant(settings, llm, ToolRegistry(), TelemetryWriter(tmp_path, "a", "p", enabled=False))
    a.handle_text("hi")
    a.turn_kept = False  # as after a failed turn
    a.replace_reply("something else")
    a.handle_text("again")
    assert history(llm)[1] == ("assistant", "first")
