"""Truthful replies on wake turns (server/wake_truth.py), and the device-control gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sasha.config import ReplyLines, Settings
from sasha.pipeline.llm import ToolCall
from sasha.server.wake_truth import TruthfulReply, claims_change, names_text, requests_change
from tests.server_fakes import WS, FakeTTS, resp
from tests.wake_fakes import frames, listen, make, segment, turn_frames, with_devices

LISTENER = f"{WS}/ws?wake=1&speak=1"


def off(entity: str) -> ToolCall:
    return ToolCall("call_service", {"domain": "light", "service": "turn_off", "entity_id": entity})


def spoken_text(tts: FakeTTS) -> str:
    return " ".join(text for text, _ in tts.spoken)


def deltas(items: list[Any]) -> str:
    return "".join(f["text"] for f in items if isinstance(f, dict) and f["type"] == "reply_delta")


def reply(items: list[Any]) -> str:
    return next(f["text"] for f in items if isinstance(f, dict) and f["type"] == "reply")


# --- live case 1: a statement-shaped transcript, a success claim, no tool call ----------


def test_claim_without_any_call_becomes_nothing_was_changed(
    settings: Settings, tmp_path: Path
) -> None:
    name = settings.assistant.wake_word.phrase
    tts = FakeTTS()
    h, stt = make(settings, tmp_path, tts=tts)
    with_devices(h)
    stt.say(f"{name} turned off all the lights in the house.")  # what Whisper wrote live
    h.llm("big-model").responses = [resp("I've turned off all the lights in the house.")]
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        got = turn_frames(ws)
    assert reply(got) == "Nothing was changed."
    assert "turned off" not in deltas(got) and "turned off" not in spoken_text(tts)
    assert spoken_text(tts) == "Nothing was changed."  # what the speech stage received


# --- live case 2: a refused 4th call, then "all off" ---------------------------------------


def test_refused_fourth_light_is_named_and_the_claim_dropped(
    settings: Settings, tmp_path: Path
) -> None:
    name = settings.assistant.wake_word.phrase
    tts = FakeTTS()
    h, stt = make(settings, tmp_path, tts=tts, speaking_tail_s=0.05)
    dev = with_devices(h)
    stt.say(f"{name}, switch off every light", "No, wait.")
    four = [off(f"light.{r}") for r in ("kitchen", "living_room", "bedroom", "hallway")]
    h.llm("big-model").responses = [resp("", four), resp("All the lights are now off.")]
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        frames(ws, "confirm")
        frames(ws, "tts_end")  # the question
        segment(ws)
        got = turn_frames(ws)
    assert len(dev.calls) == 3
    expected = (
        "I changed: Kitchen light, Living room light, Bedroom light. "
        "I didn't change: Hallway light."  # the house's own names (T3)
    )
    assert reply(got) == expected
    assert "now off" not in deltas(got) and "now off" not in spoken_text(tts)
    assert expected in spoken_text(tts)


def test_sentence_before_the_tool_call_is_still_spoken(settings: Settings, tmp_path: Path) -> None:
    name = settings.assistant.wake_word.phrase
    tts = FakeTTS()
    h, stt = make(settings, tmp_path, tts=tts)
    with_devices(h, control=False)
    stt.say(f"{name}, it's a bit bright in the kitchen")  # not a request: text streams
    h.llm("big-model").responses = [
        resp("Let me check that for you.", [off("light.kitchen")]),
        resp("The kitchen light is off now."),
    ]
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        got = turn_frames(ws)
    assert reply(got) == "Let me check that for you. I didn't change: Kitchen light."
    assert deltas(got).startswith("Let me check that for you.")
    assert "is off now" not in spoken_text(tts)


@pytest.mark.parametrize(
    "claim",
    [
        "The lights are off now.",
        "Lights off.",
        "Okay, it's dark in there now.",
        "Your living room is now bright.",
        "Sure thing, consider it handled.",
    ],
)
def test_t1_change_command_without_a_call_says_nothing_changed(
    settings: Settings, tmp_path: Path, claim: str
) -> None:
    """A false state claim can't be seen by verbs; the command decides (T1)."""
    name = settings.assistant.wake_word.phrase
    tts = FakeTTS()
    h, stt = make(settings, tmp_path, tts=tts)
    with_devices(h)
    stt.say(f"{name}, lights off")
    h.llm("big-model").responses = [resp(claim)]
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        got = turn_frames(ws)
    assert reply(got) == "Nothing was changed."
    assert deltas(got) == "Nothing was changed." and spoken_text(tts) == "Nothing was changed."


def test_t1_question_with_a_state_answer_still_streams(settings: Settings, tmp_path: Path) -> None:
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path)
    with_devices(h)
    stt.say(f"{name}, is the hallway light on?")
    h.llm("big-model").responses = [resp("Yes, the hallway light is on. Anything else?")]
    with h.client().websocket_connect(f"{WS}/ws?wake=1") as ws:
        listen(ws)
        segment(ws)
        got = turn_frames(ws)
    assert reply(got) == "Yes, the hallway light is on. Anything else?"
    streamed = [f["text"] for f in got if isinstance(f, dict) and f["type"] == "reply_delta"]
    assert len(streamed) >= 2  # sentence by sentence, not held to the end


@pytest.mark.parametrize(
    ("command", "asks"),
    [
        ("lights off", True),
        ("turn on the kitchen light", True),
        ("can you switch the fan off?", True),
        ("music louder please", True),
        ("is the hallway light on?", False),
        ("what's on tv tonight", False),
        ("tell me a joke", False),
        ("it's a bit bright in here", False),
    ],
)
def test_requests_change(command: str, asks: bool) -> None:
    assert requests_change(command, ReplyLines()) is asks


def test_state_reports_and_successes_are_left_alone(settings: Settings, tmp_path: Path) -> None:
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path)
    with_devices(h)
    stt.say(f"{name}, is the hallway light on?", f"{name}, kitchen light off")
    h.llm("big-model").responses = [
        resp("The hallway light is off."),  # a state report: no claim of change
        resp("", [off("light.kitchen")]),
        resp("I've turned off the kitchen light."),  # true: the call succeeded
    ]
    with h.client().websocket_connect(f"{WS}/ws?wake=1") as ws:  # text only: no speech tail
        listen(ws)
        segment(ws)
        first = turn_frames(ws)
        segment(ws)
        second = turn_frames(ws)
    assert reply(first) == "The hallway light is off."
    assert reply(second) == "I've turned off the kitchen light."


# --- prompt note, gate, languages -------------------------------------------------------------


def test_wake_prompt_gets_the_truth_line(settings: Settings, tmp_path: Path) -> None:
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path)
    stt.say(f"{name}, hello")
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        turn_frames(ws)
    system = h.llm("big-model").seen[0][0][0]["content"]
    assert system.endswith(h.settings.profile.wake.prompt_note)
    assert h.settings.profile.wake.prompt_note not in h.bots.main_prompt  # not the persona


def test_opt_in_needs_truthful_replies(settings: Settings, tmp_path: Path) -> None:
    h, _ = make(settings, tmp_path, truthful_replies=False)
    client = h.client()
    r = client.patch("/api/bots/voice", json={"wake_device_control": True})
    assert r.status_code == 400 and "truthful replies" in r.json()["error"]
    h2, _ = make(settings, tmp_path / "on")
    assert h2.client().patch("/api/bots/voice", json={"wake_device_control": True}).is_success


def test_switching_truthful_off_later_blocks_control(settings: Settings, tmp_path: Path) -> None:
    """The opt-in stored earlier does nothing once truthful replies are off."""
    name = settings.assistant.wake_word.phrase
    h, stt = make(settings, tmp_path)
    dev = with_devices(h)  # opt-in stored while allowed
    wake = h.settings.profile.wake.model_copy(update={"truthful_replies": False})
    h.deps.settings = h.settings.model_copy(
        update={"profile": h.settings.profile.model_copy(update={"wake": wake})}
    )
    stt.say(f"{name}, kitchen light off")
    h.llm("big-model").responses = [resp("", [off("light.kitchen")]), resp("Okay.")]
    with h.client().websocket_connect(LISTENER) as ws:
        listen(ws)
        segment(ws)
        turn_frames(ws)
    assert dev.calls == []


def test_reply_lines_follow_the_turn_language() -> None:
    class Policy:
        def __init__(self) -> None:
            self.attempted = True
            self.changed: list[tuple[str, str]] = []
            self.not_changed = [("light", "light.hallway")]

        def device_label(self, domain: str, entity: str) -> str | None:
            return {"light.hallway": "Hallway light"}.get(entity)

        def scene_names(self) -> list[str]:
            return []

    mr = ReplyLines(not_changed="बदलले नाही: {names}.")
    truth = TruthfulReply(mr, Policy())  # type: ignore[arg-type]
    assert truth.finish("ignored")[1] == "बदलले नाही: Hallway light."


def test_helpers() -> None:
    lines = ReplyLines()
    names = {f"light.l{i}": f"Lamp {i}" for i in range(8)}
    many = [("light", e) for e in names]
    assert names_text(many, lines, lambda d, e: names.get(e)).endswith("and 3 more")
    unknown = [("light", "light.x"), ("light", "light.y")]
    assert names_text(unknown, lines, lambda d, e: None) == "2 devices I don't know"
    assert claims_change("I've switched it off", lines)
    assert not claims_change("The hallway light is off.", lines)


@pytest.mark.parametrize("text", ["Done.", "All set.", "I turned it off"])
def test_claim_words(text: str) -> None:
    assert claims_change(text, ReplyLines())
