"""The server's own question back (truth.py, after the bench of 8b5ec29): a spoken "wake
me up at 7" must never end at "Nothing was set." with the question lost. Fakes only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sasha.config import Settings
from sasha.config_timers import TimersConfig
from sasha.pipeline.llm import ToolCall
from sasha.server.timers.truth import TimerTruth, pending_ask
from tests.server_fakes import WS, FakeTTS, Harness, resp
from tests.test_timers_server import with_timers
from tests.wake_fakes import turn_frames

CFG = TimersConfig()
ASKED = [{"name": "set_alarm", "ok": False, "asks": "ambiguous_period"}]
STATEMENT_ASK = "I need to know if you want the alarm for 7 AM or PM."


def typed(command: str, reply: str, calls: list[dict[str, Any]]) -> str:
    return TimerTruth(CFG, command, spoken=False).finish(reply, calls)[1]


def spoken(command: str, reply: str, calls: list[dict[str, Any]]) -> tuple[str, str]:
    truth = TimerTruth(CFG, command, spoken=True)
    truth.feed(reply)
    return truth.finish(reply, calls)


# --- 1. a timer tool asked: the server asks its fixed question -----------------------


def test_a_tools_ask_gives_the_servers_question() -> None:
    question = CFG.ask_lines["ambiguous_period"]
    assert typed("Wake me up at 7.", STATEMENT_ASK, ASKED) == f"{STATEMENT_ASK} {question}"
    assert spoken("Wake me up at 7.", STATEMENT_ASK, ASKED) == (question, question)
    # A false claim with the ask is never spoken either; on typed the question follows it.
    claim = "Done, alarm set for 7."
    assert spoken("Wake me up at 7.", claim, ASKED) == (question, question)
    assert typed("Wake me up at 7.", claim, ASKED) == f"{claim} {question}"


def test_a_typed_question_back_is_left_alone() -> None:
    assert typed("Wake me up at 7.", "Morning or evening?", ASKED) == "Morning or evening?"


@pytest.mark.parametrize(
    ("tool", "reason", "question"),
    [
        ("set_timer", "missing_time", "For how long?"),  # the tool's own line wins
        ("set_alarm", "missing_time", "For what time?"),
        ("set_reminder", "missing_text", "What should the reminder say?"),
        ("cancel_timer", "ambiguous_item", "Which one?"),
        ("set_alarm", "some_new_reason", CFG.ask_generic),  # no line: the generic one
    ],
)
def test_the_question_is_the_reasons(tool: str, reason: str, question: str) -> None:
    calls = [{"name": tool, "ok": False, "asks": reason}]
    assert spoken("Set a timer.", "Okay.", calls) == (question, question)
    assert typed("Set a timer.", "Okay.", calls) == f"Okay. {question}"


def test_a_later_success_of_the_same_tool_clears_the_ask() -> None:
    calls = [*ASKED, {"name": "set_alarm", "ok": True}]
    assert pending_ask(calls) is None
    assert typed("Wake me at 7 am.", "Alarm set for 7.", calls) == "Alarm set for 7."
    other = [*ASKED, {"name": "set_timer", "ok": True}]  # a different tool: still asked
    assert pending_ask(other) == ("set_alarm", "ambiguous_period")


def test_nothing_to_change_has_no_question() -> None:
    calls = [{"name": "cancel_timer", "ok": False, "asks": "none_active"}]
    reply = "There are no alarms to cancel."
    assert typed("Cancel all alarms.", reply, calls) == f"{reply} {CFG.nothing_set}"
    assert spoken("Cancel all alarms.", reply, calls) == (CFG.nothing_set, CFG.nothing_set)


# --- 2. no tool asked: "?" anywhere is a question back, unless a claim follows it ------


def test_a_question_anywhere_is_a_question_back() -> None:
    reply = "Morning or evening? Let me know."
    assert typed("Set an alarm for 7.", reply, []) == reply
    claim = "Morning? I've set the alarm for 7."  # a claim after the question: the line
    assert typed("Set an alarm for 7.", claim, []) == f"{claim} {CFG.nothing_set}"


# --- 3. a held spoken reply the server replaces: the line and a question ---------------


def test_a_replaced_spoken_reply_asks_again() -> None:
    said = f"{CFG.nothing_set} {CFG.ask_generic}"
    assert spoken("Wake me up at 7.", STATEMENT_ASK, []) == (said, said)
    # After a stop or cancel request the question would make no sense: the line alone.
    none = "There are no active alarms or timers to turn off."
    assert spoken("Turn off the alarm.", none, []) == (CFG.nothing_set, CFG.nothing_set)


# --- through the server: what is spoken, and the next answer read with the question -----


def test_spoken_tool_ask_says_the_servers_question(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    h = Harness(settings, tmp_path, tts=tts)
    service, _ = with_timers(h)
    h.llm("big-model").responses = [
        resp("", [ToolCall("set_alarm", {"hour": 7})]),  # no period: the tool asks
        resp("Sure, I've set your alarm for 7."),
    ]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        ws.receive_json()
        ws.receive_json()  # the timers snapshot
        ws.send_json({"type": "text", "text": "Wake me up at 7."})
        got = turn_frames(ws)
    said = " ".join(text for text, _ in tts.spoken)
    reply = next(f["text"] for f in got if isinstance(f, dict) and f["type"] == "reply")
    questions = {q for q in h.settings.profile.timers.ask_lines.values() if q}
    assert reply == said and said in questions and not service.items


def test_the_answer_to_a_server_question_is_read_with_it(
    settings: Settings, tmp_path: Path
) -> None:
    """No tool asked; the spoken reply was replaced by the line and a question, so the
    next message ("Morning.") is an answer (previous_reply_asked), read with the request."""
    h = Harness(settings, tmp_path, tts=FakeTTS())
    service, _ = with_timers(h)
    h.llm("big-model").responses = [
        resp(STATEMENT_ASK),  # no tool call, no "?": replaced
        resp("", [ToolCall("set_alarm", {"hour": 7, "period": "am"})]),
        resp("Your alarm is set for 7 in the morning."),
    ]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        ws.receive_json()
        ws.receive_json()
        for text in ("Set an alarm for 7.", "Morning."):
            ws.send_json({"type": "text", "text": text})
            turn_frames(ws)
    seen = h.llm("big-model").seen[-1][0]
    cfg = h.settings.profile.timers
    remembered = [m["content"] for m in seen if m["role"] == "assistant"]
    assert remembered[0] == f"{cfg.nothing_set} {cfg.ask_generic}"
    assert len(service.items) == 1  # the hour from the request, the period from the answer
