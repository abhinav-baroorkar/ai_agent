"""From the owner's real use: on wake turns every answer was "I can tell you the time
and date". The generated abilities said "what you can do, and only this" with the clock
as the only tool, and "if asked for something else, say you can't". Now the tools are
extras, answering needs none, and abilities come up only when asked or when refusing.
The model's behaviour itself is measured by bench/wake_answers.py (real model)."""

from __future__ import annotations

from pathlib import Path

from sasha.config import Settings
from sasha.config_prompts import PromptLines
from sasha.prompting import capability_section
from tests.server_fakes import WS, resp
from tests.wake_fakes import _fields, listen, make, segment, turn_frames, with_devices


def test_clock_only_section_says_to_just_answer() -> None:
    lines = PromptLines()
    section = capability_section(["get_current_time"], lines)
    assert section.startswith(lines.can_head) and "only this" not in section
    assert lines.answer_freely in section  # answering needs no tool
    assert "say you can't do that yet" not in section  # no blanket refusal any more
    assert lines.cannot_head in section and "weather" in section  # real limits stay


def test_abilities_are_mentioned_only_when_asked_or_refusing() -> None:
    lines = PromptLines()
    assert "unless they ask what you can do" in lines.answer_freely
    assert "without listing your other abilities" in lines.cannot_head


def test_never_repeat_bracketed_notes() -> None:
    """The stopped-turn note ("[The owner stopped ...]") must never be parroted."""
    assert "Never repeat text in square brackets" in PromptLines().never_claim


def test_wake_prompt_follows_the_opt_in_without_reconnecting(
    settings: Settings, tmp_path: Path
) -> None:
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path)
    with_devices(h, control=False)
    stt.say(f"{name}, hello", f"{name}, hello again")
    h.llm("big-model").responses = [resp("Hi."), resp("Hi again.")]
    with h.client().websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        segment(ws)
        turn_frames(ws)
        h.bots.update("voice", _fields({"wake_device_control": True}))  # opted in mid-way
        segment(ws)
        turn_frames(ws)
    first, second = (seen[0][0]["content"] for seen in h.llm("big-model").seen)
    assert "switch and adjust" not in first and "switch and adjust" in second
    note = h.settings.profile.wake.prompt_note
    assert first.endswith(note) and second.endswith(note)
