"""Speakable text (server/spoken.py) and the facts in the prompt (prompting.py).

From the owner's first test of the window: the model answered in markdown, the speech
read "asterisk asterisk" and the subtitles showed the symbols; and asked what it can do,
it claimed weather, news and timers this build doesn't have."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sasha.config import Settings
from sasha.config_prompts import PromptLines
from sasha.prompting import build_prompt, capability_section
from sasha.server.spoken import SpokenStream, clean_line, to_spoken
from tests.server_fakes import WS, FakeTTS, Harness, resp
from tests.wake_fakes import listen, make, segment, turn_frames, with_devices

MARKDOWN = """Here is what I can do:

- **Lights**: turn them on or off
- *Time*: tell you the time
1. First step
2) Second step

## Notes
Use `list_devices`, or see [the docs](http://example.org/x). Done.
> quoted line
| Room | State |
|---|:---:|
| Kitchen | on |
---
```python
print("hi")
```
"""

SPOKEN = (
    "Here is what I can do: Lights: turn them on or off. Time: tell you the time. "
    "First step. Second step. Notes. Use list_devices, or see the docs. Done. "
    'quoted line Room, State. Kitchen, on. print("hi")'
)


def test_markdown_becomes_speakable() -> None:
    assert to_spoken(MARKDOWN) == SPOKEN


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7])
def test_split_markers_never_leak(size: int) -> None:
    """Fed in pieces of any size, nothing of the markdown reaches speech."""
    stream = SpokenStream()
    pieces = [MARKDOWN[i : i + size] for i in range(0, len(MARKDOWN), size)]
    out = [stream.feed(p) for p in pieces] + [stream.finish()]
    assert "".join(out) == stream.text == SPOKEN
    assert not any(ch in "".join(out) for ch in "*`#|[]")


def test_sentences_still_stream_before_the_end() -> None:
    stream = SpokenStream()
    first = stream.feed("Sure! The **kitchen** light is on. And")
    assert first == "Sure! The kitchen light is on."  # released at once
    assert stream.feed(" the fan") == ""
    assert stream.finish() == " And the fan"


def test_a_list_marker_and_a_link_wait_until_complete() -> None:
    stream = SpokenStream()
    assert stream.feed("1") == ""
    assert stream.feed(". ") == ""
    assert stream.feed("Step one. See [the ") == "Step one."  # the marker went with it
    assert stream.feed("docs](http://x") == ""  # inside a link: held
    assert stream.feed("). Next") == " See the docs."


@pytest.mark.parametrize(
    ("line", "spoken"),
    [
        ("snake_case_name stays", "snake_case_name stays"),  # underscores inside words
        ("__bold__ and _it_", "bold and it"),
        ("~~old~~ new", "old new"),
        ("![a photo](x.png)", "a photo"),
        ("***", ""),
        ("", ""),
    ],
)
def test_clean_line(line: str, spoken: str) -> None:
    assert clean_line(line) == spoken


# --- the sockets ---------------------------------------------------------------------------------


def deltas(items: list[Any]) -> str:
    return "".join(f["text"] for f in items if isinstance(f, dict) and f["type"] == "reply_delta")


def reply(items: list[Any]) -> str:
    return next(f["text"] for f in items if isinstance(f, dict) and f["type"] == "reply")


ANSWER = "I can do a few things:\n- **Lights**: on or off\n- *Time*: tell it"


def test_home_socket_speaks_and_shows_clean_text(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    h = Harness(settings, tmp_path, tts=tts)
    h.llm("big-model").responses = [resp(ANSWER)]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "What can you do?"})
        got = turn_frames(ws)
    spoken = " ".join(text for text, _ in tts.spoken)
    want = "I can do a few things: Lights: on or off. Time: tell it."
    assert reply(got) == want and deltas(got) == want  # the subtitles match the speech
    assert "*" not in spoken and spoken.replace("  ", " ").strip() == want


def test_chat_tab_keeps_the_models_text(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path)
    h.llm("big-model").responses = [resp(ANSWER)]
    with h.client().websocket_connect(f"{WS}/ws") as ws:  # speak=0: a bot chat tab
        ws.receive_json()
        ws.send_json({"type": "text", "text": "What can you do?"})
        got = turn_frames(ws)
    assert reply(got) == ANSWER and "**Lights**" in deltas(got)


def test_listener_speaks_clean_text(settings: Settings, tmp_path: Path) -> None:
    name = settings.assistant.wake_word.phrase
    tts = FakeTTS()
    h, stt = make(settings, tmp_path, tts=tts)
    stt.say(f"{name}, what can you do")
    h.llm("big-model").responses = [resp("**Quite a lot**, really.")]
    with h.client().websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        segment(ws)
        got = turn_frames(ws)
    assert reply(got) == "Quite a lot, really."
    assert all("*" not in text for text, _ in tts.spoken)


# --- the prompt: facts about the tools, and the spoken style -------------------------------------


def test_no_tools_says_so() -> None:
    lines = PromptLines()
    section = capability_section([], lines)
    assert lines.no_tools in section and lines.can_head not in section
    assert "weather" in section and "internet" in section and "timers" in section
    assert lines.never_claim in section


def test_tools_become_abilities_and_lift_limits() -> None:
    lines = PromptLines()
    section = capability_section(["get_current_time", "search", "click", "read_page"], lines)
    assert "date and time" in section and section.count("search the web") == 1
    assert "internet" not in section and "weather" not in section  # the browser can
    assert "set timers, alarms or reminders" in section  # still a limit
    assert "use the tool frobnicate" in capability_section(["frobnicate"], lines)


def test_spoken_note_only_for_spoken_conversations() -> None:
    lines = PromptLines()
    assert lines.spoken_note in build_prompt("Base.", [], lines, spoken=True)
    assert lines.spoken_note not in build_prompt("Base.", [], lines, spoken=False)


def test_prompts_on_each_socket(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path)
    h.llm("big-model").responses = [resp("ok"), resp("ok")]
    client = h.client()
    for path in ("/ws?speak=1", "/ws"):
        with client.websocket_connect(f"{WS}{path}") as ws:
            ws.receive_json()
            ws.send_json({"type": "text", "text": "hi"})
            turn_frames(ws)
    home, tab = (seen[0][0]["content"] for seen in h.llm("big-model").seen)
    note = h.settings.profile.prompt_lines["en"].spoken_note
    assert note in home and note not in tab
    assert "date and time" in home and "date and time" in tab  # the clock is real
    assert h.bots.main_prompt in home  # the persona prompt is untouched, then the facts


@pytest.mark.parametrize("control", [False, True])
def test_listener_claims_device_control_only_when_allowed(
    settings: Settings, tmp_path: Path, control: bool
) -> None:
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path)
    with_devices(h, control=control)
    stt.say(f"{name}, hello")
    h.llm("big-model").responses = [resp("Hi.")]
    with h.client().websocket_connect(f"{WS}/ws?wake=1&speak=1") as ws:
        listen(ws)
        segment(ws)
        turn_frames(ws)
    system = h.llm("big-model").seen[0][0][0]["content"]
    assert ("switch and adjust" in system) is control
    assert "check the smart-home devices" in system
    assert system.endswith(h.settings.profile.wake.prompt_note)
