"""Re-review of 4d98ddc (S1-S6 and a nit): answers to the tool's own question, the
model's content in the words, contradicting cues, the hour said, and claims inside a
question. Fake clock, invented zone (ToyZone)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sasha.config import ReplyLines
from sasha.conversation import TurnContext, new_turn
from sasha.server.timers.service import TimerService
from sasha.server.timers.tools import TimerAsk, add_timer_tools
from sasha.server.wake_truth import TruthfulReply
from sasha.tools import ToolRegistry
from tests.test_timers_review import AskedPolicy
from tests.timer_fakes import ZONE, FakeClock, FakeTarget, at, make_service


class Turns:
    """The owner's messages, turn by turn, as `current_turn` gives them to the tools."""

    def __init__(self, text: str) -> None:
        self.turn = new_turn((text,), "text")

    def say(self, text: str) -> None:
        self.turn = new_turn((*self.turn.user_messages, text), "text")

    def __call__(self) -> TurnContext:
        return self.turn


def tools(service: TimerService, turns: Turns) -> ToolRegistry:
    return add_timer_tools(ToolRegistry(), service, said=turns)


def due(service: TimerService, result: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(service.items[result["id"]].due, ZONE)


def one(service: TimerService, said: str, name: str, **args: Any) -> dict[str, Any]:
    return tools(service, Turns(said)).get(name).handler(**args)


# --- S1: the answer is checked together with the question's turn -----------------------------


@pytest.mark.parametrize(
    ("first", "args1", "answer", "args2", "want"),
    [
        ("Set an alarm for 7.", {"hour": 7}, "Evening.", {"hour": 7, "period": "pm"},
         datetime(2031, 3, 27, 19, 0, tzinfo=ZONE)),
        ("Set an alarm for 7:30.", {"hour": 7, "minute": 30}, "In the morning.",
         {"hour": 7, "minute": 30, "period": "am"}, datetime(2031, 3, 27, 7, 30, tzinfo=ZONE)),
        ("Wake me at 7.", {"hour": 7}, "Tomorrow morning please.",
         {"hour": 7, "period": "am", "day": "tomorrow"}, datetime(2031, 3, 28, 7, 0, tzinfo=ZONE)),
    ],
)  # fmt: skip
def test_s1_answers_to_the_period_question(
    tmp_path: Path, first: str, args1: dict[str, Any], answer: str, args2: dict[str, Any],
    want: datetime,
) -> None:  # fmt: skip
    service, _, _ = make_service(tmp_path)  # Thursday 06:00
    turns = Turns(first)
    alarm = tools(service, turns).get("set_alarm").handler
    with pytest.raises(TimerAsk):
        alarm(**args1)
    turns.say(answer)
    assert due(service, alarm(**args2)) == want


def test_s1_answer_with_the_delay(tmp_path: Path) -> None:
    service, _, clock = make_service(tmp_path)
    turns = Turns("Remind me to check the oven.")
    remind = tools(service, turns).get("set_reminder").handler
    with pytest.raises(TimerAsk):
        remind(text="check the oven", in_minutes=15)  # made up
    turns.say("In 20 minutes.")
    assert service.items[remind(text="check the oven", in_minutes=20)["id"]].due == clock() + 1200


def test_s1_is_forgotten_after_one_more_turn_or_a_success(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    turns = Turns("Set an alarm for 7.")
    registry = tools(service, turns)
    alarm = registry.get("set_alarm").handler
    with pytest.raises(TimerAsk):
        alarm(hour=7)
    turns.say("What's the weather like?")  # a turn with no timer call
    turns.say("Evening.")
    with pytest.raises(TimerAsk) as err:
        alarm(hour=7, period="pm")  # the question is two turns back: forgotten
    assert err.value.reason == "not_said"
    turns.say("Set an alarm for 7 in the evening.")
    alarm(hour=7, period="pm")
    turns.say("And one in the morning.")
    with pytest.raises(TimerAsk):
        alarm(hour=7, period="am")  # the success forgot the earlier words: no number now


# --- S2, S4: the model's own content is not the time -----------------------------------------


@pytest.mark.parametrize(
    ("said", "name", "args"),
    [
        ("Remind me to take 2 pills.", "set_reminder",
         {"text": "take 2 pills", "hour": 9, "period": "am"}),
        ("Remind me to call the 3 kids tonight.", "set_reminder",
         {"text": "call the 3 kids", "hour": 8, "period": "pm"}),
        ("Set a timer for 2 eggs.", "set_timer", {"minutes": 10, "label": "2 eggs"}),
    ],
)  # fmt: skip
def test_s2_numbers_in_the_content_are_not_the_time(
    tmp_path: Path, said: str, name: str, args: dict[str, Any]
) -> None:
    service, _, _ = make_service(tmp_path, FakeClock(at(2031, 3, 27, 10)))
    with pytest.raises(TimerAsk) as err:
        one(service, said, name, **args)
    assert err.value.reason == "not_said" and not service.items


def test_s2_a_label_that_restates_the_time_is_dropped(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    result = one(service, "Set a timer for ten minutes.", "set_timer", minutes=10,
                 label="ten minutes")  # fmt: skip
    assert service.items[result["id"]].label == ""


def test_s4_a_cue_in_the_content_is_not_a_cue(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)  # 06:00: 07:00 and 19:00 both ahead
    with pytest.raises(TimerAsk) as err:
        one(service, "Remind me at 7 to say good night.", "set_reminder",
            text="say good night", hour=7, period="pm")  # fmt: skip
    assert err.value.reason == "ambiguous_period"


# --- S3: a cue that contradicts the period ------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "args", "ok"),
    [
        ("Give me a timer for two and a half minutes.", {"minutes": 2}, False),  # the bench
        ("Give me a timer for two and a half minutes.", {"minutes": 2, "seconds": 30}, True),
        ("Set a timer for an hour and a half.", {"minutes": 90}, True),
        ("A timer for a quarter of an hour.", {"minutes": 15}, True),
        ("A timer for a quarter of an hour.", {"minutes": 25}, False),
    ],
)
def test_a_half_or_quarter_said_must_be_in_the_delay(
    tmp_path: Path, said: str, args: dict[str, Any], ok: bool
) -> None:
    service, _, _ = make_service(tmp_path)
    if ok:
        one(service, said, "set_timer", **args)
        assert len(service.items) == 1
    else:
        with pytest.raises(TimerAsk):
            one(service, said, "set_timer", **args)


@pytest.mark.parametrize("said", ["At 6:15.", "Wake me at 6.15 please"])
def test_minutes_are_no_evening_cue(tmp_path: Path, said: str) -> None:
    """Bench run of fdc7aa6: "At 6:15." with am made the 15 a pm cue (18:15, wrong)."""
    service, _, _ = make_service(tmp_path)
    with pytest.raises(TimerAsk) as err:  # am made up and dropped: morning or evening?
        one(service, said, "set_alarm", hour=6, minute=15, period="am", day="tomorrow")
    assert err.value.reason == "ambiguous_period"
    service2, _, _ = make_service(tmp_path / "b")
    result = one(service2, "Set an alarm for 18:15.", "set_alarm", hour=18, minute=15)
    assert due(service2, result).hour == 18  # a real 24-hour time still counts


def test_s3_the_persons_cue_wins(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    result = one(service, "Wake me at 7 in the morning.", "set_alarm", hour=7, period="pm")
    assert due(service, result) == datetime(2031, 3, 27, 7, 0, tzinfo=ZONE)


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("Wake me at 7 in the morning.", {"hour": 19}),  # a 13-23 hour against "morning"
        ("Remind me at 7 tonight, no, tomorrow morning.", {"hour": 7, "period": "pm"}),
    ],
)
def test_s3_disagreement_asks(tmp_path: Path, said: str, args: dict[str, Any]) -> None:
    service, _, _ = make_service(tmp_path)
    name = "set_alarm" if "Wake" in said else "set_reminder"
    extra = {} if name == "set_alarm" else {"text": "x"}
    with pytest.raises(TimerAsk) as err:
        one(service, said, name, **args, **extra)
    assert err.value.reason == "ambiguous_period"


# --- S5: the hour is one that was said ------------------------------------------------------------


def test_s5_the_hour_must_be_said(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    with pytest.raises(TimerAsk) as err:
        one(service, "Alarm at 7 in the morning.", "set_alarm", hour=8, period="am")
    assert err.value.reason == "not_said"
    quarter = one(service, "Set an alarm for quarter to eight tomorrow morning.", "set_alarm",
                  hour=7, minute=45, period="am", day="tomorrow")  # fmt: skip
    assert due(service, quarter) == datetime(2031, 3, 28, 7, 45, tzinfo=ZONE)
    seven = one(service, "Wake me at seven in the morning.", "set_alarm", hour=7, period="am")
    assert due(service, seven).hour == 7  # a number word counts


# --- S6: a question that also claims -------------------------------------------------


@pytest.mark.parametrize(
    ("model", "said"),
    [
        ("I've turned the lights off, morning or evening?",
         "Nothing was changed. Morning or evening?"),
        ("I've set it for tomorrow?", f"Nothing was changed. {ReplyLines().ask_again}"),
        ("Should I set it for the morning or the evening?",
         "Should I set it for the morning or the evening?"),
    ],
)  # fmt: skip
def test_s6_claims_inside_a_question(model: str, said: str) -> None:
    truth = TruthfulReply(ReplyLines(), AskedPolicy(), "set an alarm for seven")  # type: ignore[arg-type]
    truth.feed(model)
    assert truth.finish(model)[1] == said


# --- nit: a repeating alarm that moved on is still counted in the missed line ------------------


def test_moved_on_repeating_alarm_is_said(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    item = service.items[one(service, "Wake me at 7 am on weekdays.", "set_alarm", hour=7,
                             period="am", repeat="weekdays")["id"]]  # fmt: skip
    clock.now = item.due
    service.tick()  # nobody connected: pending
    clock.advance(30 * 60)
    home = FakeTarget()
    hub.add(home)
    assert item.state == "scheduled" and home.rings[-1][1] == "You have 1 missed alarm."
    assert not any(f["type"] == "timer_ringing" for f in home.frames)  # nothing to show
