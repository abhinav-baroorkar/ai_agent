"""The re-review of b253796, Q2: only a real request is held; a status question answered
without list_timers gets the real state from the service. Fakes only (fake clock, the
invented zone of tests/timer_fakes.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sasha.config import Settings
from sasha.config_timers import TimersConfig
from sasha.pipeline.llm import ToolCall
from sasha.server.timers.model import TimerItem
from sasha.server.timers.service import TimerService
from sasha.server.timers.status import kinds_asked, status_line
from sasha.server.timers.truth import TimerTruth, asks_for_timer
from tests.server_fakes import WS, FakeTTS, Harness, resp
from tests.test_timers_server import with_timers
from tests.timer_fakes import at, make_service
from tests.wake_fakes import turn_frames

CFG = TimersConfig()


@pytest.mark.parametrize(
    ("command", "reply"),
    [  # mention-only commands: no request, so the model's answer is spoken as it is
        ("thanks for the reminder", "You're welcome!"),
        ("my alarm app is broken, any tips", "Try restarting your phone."),
        ("what's a good timer for tea", "Three to five minutes is typical."),
        ("tell me about timers", "A timer counts down."),
        ("my alarm didn't go off this morning", "Sorry to hear that."),
        ("what's a good timer for tea?", "Three to five minutes is typical."),
    ],
)
def test_a_mention_keeps_the_models_answer(command: str, reply: str) -> None:
    assert not asks_for_timer(command, CFG)
    truth = TimerTruth(CFG, command, spoken=True, state=lambda _w: "STATE")
    assert not truth.holding and truth.feed(reply) == reply
    assert truth.finish(reply, []) == ("", reply)


@pytest.mark.parametrize(
    "command",
    ["set an alarm", "alarm at 7", "ten minute timer", "turn off the alarm", "cancel my timer",
     "I need a reminder tonight", "timer for half an hour", "wake me", "snooze"],
)  # fmt: skip
def test_requests_still_count(command: str) -> None:
    assert asks_for_timer(command, CFG)


def service_with_items(tmp_path: Path) -> TimerService:
    service, _, _ = make_service(tmp_path)
    service.add(TimerItem("", "alarm", at(2031, 3, 28, 7, 0)))  # tomorrow 07:00
    service.add(TimerItem("", "timer", service.clock() + 300))
    service.add(TimerItem("", "reminder", at(2031, 3, 27, 17, 0), text="SECRET-TEXT"))
    return service


def test_the_status_line_says_the_real_state(tmp_path: Path) -> None:
    service = service_with_items(tmp_path)
    assert status_line(service, {"alarm"}) == "Alarm: 7:00 AM tomorrow."
    assert status_line(service, {"timer"}) == "Timer: 5 minutes left."
    everything = status_line(service, kinds_asked(["what", "is", "set"]))
    assert "Reminder: 5:00 PM today." in everything and "SECRET" not in everything
    empty, _, _ = make_service(tmp_path / "empty")
    assert status_line(empty, {"alarm"}) == CFG.status_none["alarm"]
    assert status_line(empty, kinds_asked([])) == CFG.status_none["all"]


def test_a_status_question_without_list_timers_gets_the_state() -> None:
    state = "Alarm: 7:00 AM tomorrow."
    typed = TimerTruth(CFG, "Is my alarm set?", spoken=False, state=lambda _w: state)
    reply = "Yes, it's set for 7."
    assert typed.finish(reply, []) == (f" {state}", f"{reply} {state}")
    held = TimerTruth(CFG, "Is my alarm set?", spoken=True, state=lambda _w: state)
    held.feed(reply)
    assert held.finish(reply, []) == (state, state)  # the guess is never spoken
    listed = TimerTruth(CFG, "Is my alarm set?", spoken=True, state=lambda _w: state)
    listed.feed(reply)
    assert listed.finish(reply, [{"name": "list_timers", "ok": True}]) == (reply, reply)


def test_spoken_status_question_over_the_socket(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    h = Harness(settings, tmp_path, tts=tts)
    service, _ = with_timers(h)
    h.llm("big-model").responses = [
        resp("", [ToolCall("set_alarm", {"hour": 7, "period": "am", "day": "tomorrow"})]),
        resp("Your alarm is set for 7 tomorrow morning."),
        resp("No, you don't have any alarms."),  # a wrong guess, no list_timers call
    ]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        ws.receive_json()
        ws.receive_json()
        for text in ("Set an alarm for 7 am tomorrow.", "Is my alarm set?"):
            before = len(tts.spoken)
            ws.send_json({"type": "text", "text": text})
            turn_frames(ws)
    said = " ".join(t for t, _ in tts.spoken[before:])
    assert len(service.items) == 1 and said.startswith("Alarm: 7:00 AM")
    assert "don't" not in said
