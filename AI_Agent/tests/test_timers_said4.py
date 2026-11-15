"""Re-review of ef4629a: B1 (a number in the content isn't a time), B2 (a new request
doesn't borrow the previous message), B3 (a delay must be the number said), and the
typed and window turns' correction line. Fake clock, invented zone (ToyZone)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sasha.config import Settings
from sasha.config_timers import TimersConfig
from sasha.conversation import TurnContext, new_turn
from sasha.pipeline.llm import ToolCall
from sasha.server.timers.service import TimerService
from sasha.server.timers.tools import TimerAsk, add_timer_tools
from sasha.server.timers.truth import claims_timer
from sasha.tools import ToolRegistry
from tests.server_fakes import WS, FakeTTS, Harness, resp
from tests.test_timers_server import with_timers
from tests.timer_fakes import ZONE, FakeClock, at, make_service
from tests.wake_fakes import frames, turn_frames


class Talk:
    def __init__(self, first: str) -> None:
        self.turn = new_turn((first,), "text")

    def then(self, text: str, asked: bool = True) -> None:
        self.turn = new_turn((*self.turn.user_messages, text), "text", asked, 10.0)

    def __call__(self) -> TurnContext:
        return self.turn


def tool(service: TimerService, talk: Talk, name: str) -> Any:
    return add_timer_tools(ToolRegistry(), service, said=talk).get(name).handler


def at10(tmp: Path) -> TimerService:
    return make_service(tmp, FakeClock(at(2031, 3, 27, 10)))[0]


def asks(fn: Any, **args: Any) -> str:
    with pytest.raises(TimerAsk) as err:
        fn(**args)
    return err.value.reason


# --- B1: a number in the content is no time, however the model words the text -----------


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("remind me to take 2 pills", {"text": "pills", "hour": 2, "period": "pm"}),
        ("remind me to call the 3 kids tonight", {"text": "call kids", "hour": 3, "period": "pm"}),
        ("remind me to buy 2 things at the store",
         {"text": "buy things at the store", "hour": 2, "period": "pm"}),
    ],
)  # fmt: skip
def test_b1_content_numbers_are_no_time(tmp_path: Path, said: str, args: dict[str, Any]) -> None:
    service = at10(tmp_path)
    assert asks(tool(service, Talk(said), "set_reminder"), **args) == "not_said"
    assert not service.items


def test_b1_a_time_said_still_counts(tmp_path: Path) -> None:
    service = at10(tmp_path)
    remind = tool(service, Talk("remind me at 2 to take 2 pills"), "set_reminder")
    result = remind(text="take pills", hour=2, period="pm")
    assert datetime.fromtimestamp(service.items[result["id"]].due, ZONE).hour == 14


def test_wake_me_at_7_not_8(tmp_path: Path) -> None:
    """The review's note: a number after "not" isn't in a time position."""
    service = at10(tmp_path)
    assert asks(tool(service, Talk("Wake me at 7, not 8"), "set_alarm"), hour=8) == "not_said"


# --- B2: a new request is read alone -----------------------------------------------------------


def test_b2_after_a_tool_question(tmp_path: Path) -> None:
    service = at10(tmp_path)
    talk = Talk("set an alarm for 7")
    alarm = tool(service, talk, "set_alarm")
    assert asks(alarm, hour=7) == "ambiguous_period"
    talk.then("remind me to call mom")  # a new request, not the answer
    remind = add_timer_tools(ToolRegistry(), service, said=talk).get("set_reminder").handler
    assert asks(remind, text="call mom", hour=7, period="pm") == "not_said"


@pytest.mark.parametrize(
    ("first", "second", "name", "args"),
    [
        ("I have 3 eggs, how long to boil?", "set a timer", "set_timer", {"minutes": 3}),
        ("is it cold at 6 am?", "remind me to call mom", "set_reminder",
         {"text": "call mom", "hour": 6, "period": "am"}),
        ("The meeting moved from 3 to 4.", "ok remind me", "set_reminder",
         {"text": "meeting", "hour": 3}),
    ],
)  # fmt: skip
def test_b2_after_the_models_own_question(
    tmp_path: Path, first: str, second: str, name: str, args: dict[str, Any]
) -> None:
    service = at10(tmp_path)
    talk = Talk(first)
    talk.then(second)
    assert asks(tool(service, talk, name), **args) == "not_said"


def test_b2_an_answer_still_combines(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path, FakeClock(at(2031, 3, 27, 21)))
    talk = Talk("Set an alarm for 7.")
    talk.then("Morning.")
    tool(service, talk, "set_alarm")(hour=7, period="am")
    assert len(service.items) == 1


# --- B3: the delay is a number said ----------------------------------------------------------

POTS = "remind me in 5 to check the 3 pots"


@pytest.mark.parametrize(
    ("said", "name", "args", "ok"),
    [
        (POTS, "set_reminder", {"text": "check pots", "in_minutes": 3}, False),
        (POTS, "set_reminder", {"text": "check pots", "in_minutes": 5}, True),
        ("Set a timer for 5 minutes.", "set_timer", {"minutes": 5, "seconds": 40}, False),
        ("Set a timer for 1 hour 20 minutes.", "set_timer", {"minutes": 80}, True),
        ("Timer for 90 seconds.", "set_timer", {"seconds": 90}, True),
        ("Remind me in half an hour.", "set_reminder", {"text": "x", "in_minutes": 30}, True),
        ("Remind me in an hour and a half.", "set_reminder", {"text": "x", "in_minutes": 90}, True),
        ("Remind me in a couple of hours.", "set_reminder", {"text": "x", "in_hours": 2}, True),
        ("Twenty five minutes timer.", "set_timer", {"minutes": 25}, True),
    ],
)  # fmt: skip
def test_b3_the_delay_is_one_said(
    tmp_path: Path, said: str, name: str, args: dict[str, Any], ok: bool
) -> None:
    service = at10(tmp_path)
    handler = tool(service, Talk(said), name)
    if ok:
        handler(**args)
        assert len(service.items) == 1
    else:
        assert asks(handler, **args) == "not_said"


# --- typed and window turns: no claimed timer change without the tool ---------------------


@pytest.mark.parametrize(
    ("text", "claims"),
    [
        ("I've set an alarm for 7:00.", True),
        ("Your timer is set for ten minutes.", True),
        ("Done! The reminder was created.", True),
        ("Should I set an alarm for 7?", False),
        ("I can't set alarms yet.", False),
        ("The weather is nice.", False),
    ],
)
def test_claims_timer(text: str, claims: bool) -> None:
    assert claims_timer(text, TimersConfig()) is claims


def reply_of(items: list[Any]) -> str:
    return next(f["text"] for f in items if isinstance(f, dict) and f["type"] == "reply")


def test_typed_claim_without_a_tool_gets_the_line(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path)
    with_timers(h)
    h.llm("big-model").responses = [resp("I've set an alarm for 7:00.")]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        ws.receive_json()
        ws.receive_json()  # the timers snapshot
        ws.send_json({"type": "text", "text": "Set an alarm for 7 am."})
        got = turn_frames(ws)
    line = h.settings.profile.timers.nothing_set
    assert reply_of(got) == f"I've set an alarm for 7:00. {line}"
    streamed = "".join(f["text"] for f in got if isinstance(f, dict) and f["type"] == "reply_delta")
    assert streamed.endswith(line)


def test_window_turn_never_speaks_the_false_claim(settings: Settings, tmp_path: Path) -> None:
    tts = FakeTTS()
    h = Harness(settings, tmp_path, tts=tts)
    with_timers(h)
    h.llm("big-model").responses = [resp("Sure. I've set an alarm for 7:00 tomorrow.")]
    with h.client().websocket_connect(f"{WS}/ws?speak=1") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "text", "text": "Set an alarm for 7 am tomorrow."})
        got = frames(ws, "state", "idle")
    line = h.settings.profile.timers.nothing_set
    assert reply_of(got) == line
    assert [text for text, _ in tts.spoken] == [line]  # the claim was never spoken


def test_a_real_timer_change_stands(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path)
    service, _ = with_timers(h)
    h.llm("big-model").responses = [
        resp("", [ToolCall("set_timer", {"minutes": 10})]),
        resp("I've set a timer for 10 minutes."),
    ]
    with h.client().websocket_connect(f"{WS}/ws") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "text", "text": "Set a timer for 10 minutes."})
        got = turn_frames(ws)
    assert reply_of(got) == "I've set a timer for 10 minutes." and len(service.items) == 1
