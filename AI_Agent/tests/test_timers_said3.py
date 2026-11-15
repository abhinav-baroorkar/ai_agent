"""An answer to the assistant's own question ("Set an alarm for 7." / "Morning."): read
with the previous message when the previous reply ended in "?", the previous turn made
no item, and it was recent. Fake clock, invented zone (ToyZone)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sasha.config import Settings
from sasha.conversation import TurnContext, current_turn, new_turn
from sasha.orchestrator import Assistant
from sasha.pipeline.llm import ToolCall
from sasha.server.timers.service import TimerService
from sasha.server.timers.tools import TimerAsk, add_timer_tools
from sasha.telemetry import TelemetryWriter
from sasha.tools import Tool, ToolRegistry
from tests.server_fakes import FakeLLM, resp
from tests.timer_fakes import ZONE, FakeClock, at, make_service


class Talk:
    """Turns as the orchestrator builds them: messages, whether the last reply asked,
    and the seconds since the previous turn."""

    def __init__(self, first: str) -> None:
        self.turn = new_turn((first,), "text")

    def answer(self, text: str, asked: bool = True, since: float = 10.0) -> None:
        self.turn = new_turn((*self.turn.user_messages, text), "text", asked, since)

    def __call__(self) -> TurnContext:
        return self.turn


def handler(service: TimerService, talk: Talk, name: str) -> Any:
    return add_timer_tools(ToolRegistry(), service, said=talk).get(name).handler


def due(service: TimerService, result: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(service.items[result["id"]].due, ZONE)


# --- the two bench cases ---------------------------------------------------------------------


def test_morning_answers_the_models_own_question(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path, FakeClock(at(2031, 3, 27, 21)))
    talk = Talk("Set an alarm for 7.")  # the model asked "morning or evening?" itself
    talk.answer("Morning.")
    result = handler(service, talk, "set_alarm")(hour=7, period="am")
    assert due(service, result) == datetime(2031, 3, 28, 7, 0, tzinfo=ZONE)


def test_a_time_answers_the_models_own_question(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    talk = Talk("Wake me up tomorrow morning.")
    talk.answer("At 6:15.")
    result = handler(service, talk, "set_alarm")(hour=6, minute=15, period="am", day="tomorrow")
    assert due(service, result) == datetime(2031, 3, 28, 6, 15, tzinfo=ZONE)


# --- when not to combine ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asked", "since"),
    [(False, 10.0), (True, 300.0)],  # the reply asked nothing; the question is stale
)
def test_no_combine(tmp_path: Path, asked: bool, since: float) -> None:
    service, _, _ = make_service(tmp_path, FakeClock(at(2031, 3, 27, 21)))
    talk = Talk("Set an alarm for 7.")
    talk.answer("Morning.", asked=asked, since=since)
    with pytest.raises(TimerAsk) as err:
        handler(service, talk, "set_alarm")(hour=7, period="am")
    assert err.value.reason == "not_said"  # "Morning." alone has no time


def test_no_combine_after_a_turn_that_made_an_item(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    talk = Talk("Set an alarm for 7 am.")
    alarm = handler(service, talk, "set_alarm")
    alarm(hour=7, period="am")  # made: "Done. Anything else?" asks, but lends nothing
    talk.answer("And one for 8 in the evening?")  # its own words suffice
    alarm(hour=8, period="pm")
    talk.answer("Morning.")
    with pytest.raises(TimerAsk) as err:
        alarm(hour=9, period="am")
    assert err.value.reason == "not_said"


# --- the review's S2 and S3 probes still hold on combined words ---------------------------


def test_s2_content_numbers_still_dont_count_when_combined(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    talk = Talk("Remind me to take 2 pills.")
    talk.answer("Tonight.")
    with pytest.raises(TimerAsk) as err:
        handler(service, talk, "set_reminder")(text="take 2 pills", hour=9, period="pm")
    assert err.value.reason == "not_said"


def test_s3_the_cue_still_wins_and_contradictions_ask(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    talk = Talk("Set an alarm for 7.")
    talk.answer("In the morning.")
    result = handler(service, talk, "set_alarm")(hour=7, period="pm")  # pm made up
    assert due(service, result).hour == 7
    talk2 = Talk("Set an alarm for 7 tonight.")
    talk2.answer("No, in the morning.")
    with pytest.raises(TimerAsk) as err:
        handler(service, talk2, "set_alarm")(hour=7, period="am")
    assert err.value.reason == "ambiguous_period"


# --- the orchestrator fills the fields ------------------------------------------------------


def test_orchestrator_sets_previous_reply_asked_and_since(
    settings: Settings, tmp_path: Path
) -> None:
    seen: list[TurnContext | None] = []
    probe = Tool("probe", "p", {"type": "object", "properties": {}},
                 lambda: seen.append(current_turn.get()) or {})  # fmt: skip
    llm = FakeLLM("m")
    llm.responses = [
        resp("", [ToolCall("probe", {})]), resp("Morning or evening?"),
        resp("", [ToolCall("probe", {})]), resp("Done."),
        resp("", [ToolCall("probe", {})]), resp("Done."),
    ]  # fmt: skip
    telemetry = TelemetryWriter(tmp_path, "a", "p", enabled=False)
    assistant = Assistant(settings, llm, ToolRegistry([probe]), telemetry)
    now = [100.0]
    assistant.clock = lambda: now[0]
    assistant.handle_text("Set an alarm for 7.")
    now[0] += 30
    assistant.handle_text("Morning.")
    now[0] += 5
    assistant.handle_text("Thanks.")
    first, second, third = seen
    assert first is not None and second is not None and third is not None
    assert not first.previous_reply_asked and first.since_previous_s is None
    assert second.previous_reply_asked and second.since_previous_s == 30
    assert not third.previous_reply_asked  # "Done." asked nothing
    assert "Morning or evening?" not in repr(second)  # only the fact, never the text
