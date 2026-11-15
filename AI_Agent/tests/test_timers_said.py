"""The said-check (server/timers/said.py): a field the person's words don't support is
dropped or asked about, never kept. Each case is the bench's real failure or its
neighbour. Fake clock, invented zone (ToyZone)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sasha.config_timers import TimeCues
from sasha.server.timers.said import Said
from sasha.server.timers.service import TimerService
from sasha.server.timers.tools import TimerAsk, add_timer_tools
from sasha.telemetry import TelemetryWriter
from sasha.tools import ToolRegistry
from tests.timer_fakes import ZONE, FakeClock, at, make_service


def tool(service: TimerService, name: str, said: str) -> Any:
    return add_timer_tools(ToolRegistry(), service, said=lambda: said).get(name).handler


def due_of(service: TimerService, result: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(service.items[result["id"]].due, ZONE)


# --- (a) a period nobody said is dropped, then the ask rules apply ---------------------------


@pytest.mark.parametrize(
    ("now", "said", "name", "args"),
    [
        ((23, 30), "Remind me at 1 to feed the cat.", "set_reminder",
         {"text": "feed the cat", "hour": 1, "minute": 0, "period": "am"}),
        ((13, 0), "Alarm at 3.", "set_alarm", {"hour": 3, "minute": 0, "period": "am"}),
        ((6, 0), "Wake me at seven.", "set_alarm", {"hour": 19}),  # "seven" sent as 19
        ((6, 0), "Wake me at seven.", "set_alarm", {"hour": 7, "period": "am"}),
    ],
)  # fmt: skip
def test_a_made_up_period_makes_it_ask(
    tmp_path: Path, now: tuple[int, int], said: str, name: str, args: dict[str, Any]
) -> None:
    service, _, _ = make_service(tmp_path, FakeClock(at(2031, 3, 27, *now)))
    with pytest.raises(TimerAsk) as err:
        tool(service, name, said)(**args)
    assert err.value.reason == "ambiguous_period" and not service.items


def test_a_reminder_without_the_period_uses_the_plain_rule(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path, FakeClock(at(2031, 3, 27, 8)))
    args = {"text": "stretch", "hour": 7, "minute": 30, "period": "am"}  # am made up
    result = tool(service, "set_reminder", "Remind me at 7:30 to stretch.")(**args)
    assert due_of(service, result) == datetime(2031, 3, 27, 19, 30, tzinfo=ZONE)


@pytest.mark.parametrize(
    ("said", "args", "hour"),
    [
        ("Set an alarm for 19:00.", {"hour": 19}, 19),
        ("Set an alarm for 6:30 pm.", {"hour": 18, "minute": 30, "period": "pm"}, 18),
        ("Wake me at 7am.", {"hour": 7, "period": "am"}, 7),
        ("Wake me at 7 a.m. please", {"hour": 7, "period": "am"}, 7),
        ("Alarm at half past six in the evening.", {"hour": 6, "minute": 30, "period": "pm"}, 18),
        ("Set an alarm for noon.", {"hour": 12, "period": "pm"}, 12),
        ("Alarm at midnight.", {"hour": 0, "period": "am"}, 0),
    ],
)
def test_a_said_periods_are_kept(
    tmp_path: Path, said: str, args: dict[str, Any], hour: int
) -> None:
    service, _, _ = make_service(tmp_path)
    assert due_of(service, tool(service, "set_alarm", said)(**args)).hour == hour


def test_a_tonight_is_a_period(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    args = {"text": "lock the door", "hour": 20}
    result = tool(service, "set_reminder", "Remind me at 8 tonight to lock the door.")(**args)
    assert due_of(service, result).hour == 20


# --- (b) a clock time needs a number or a time word ------------------------------------------


@pytest.mark.parametrize(
    ("name", "said", "args"),
    [
        ("set_alarm", "Wake me up tomorrow morning.",
         {"hour": 7, "period": "am", "day": "tomorrow"}),
        ("set_reminder", "Remind me tonight to call my sister.",
         {"text": "call", "hour": 8, "period": "pm"}),
    ],
)  # fmt: skip
def test_b_no_time_said_asks(tmp_path: Path, name: str, said: str, args: dict[str, Any]) -> None:
    service, _, _ = make_service(tmp_path)
    with pytest.raises(TimerAsk) as err:
        tool(service, name, said)(**args)
    assert err.value.reason == "not_said"


# --- (c) a delay needs a number or a duration word ---------------------------------------------


@pytest.mark.parametrize(
    ("name", "said", "args"),
    [
        ("set_reminder", "Remind me to check the oven.",
         {"text": "check the oven", "in_minutes": 15}),
        ("set_timer", "Set a timer.", {"minutes": 5}),
    ],
)  # fmt: skip
def test_c_no_delay_said_asks(tmp_path: Path, name: str, said: str, args: dict[str, Any]) -> None:
    service, _, _ = make_service(tmp_path)
    with pytest.raises(TimerAsk) as err:
        tool(service, name, said)(**args)
    assert err.value.reason == "not_said" and not service.items


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("Remind me in an hour to call back.", {"text": "call back", "in_hours": 1}),
        ("Remind me in half an hour to move the car.", {"text": "move", "in_minutes": 30}),
        ("Remind me in a couple of hours.", {"text": "x", "in_hours": 2}),
    ],
)
def test_c_said_delays_are_kept(tmp_path: Path, said: str, args: dict[str, Any]) -> None:
    service, _, _ = make_service(tmp_path)
    tool(service, "set_reminder", said)(**args)
    assert len(service.items) == 1


def test_said_tokens() -> None:
    words = Said("Wake me at 7a.m., then at 19:30", [TimeCues()])
    assert words.has_period() and words.says_hour(19) and words.says_hour(7)
    assert not Said("Remind me to check the oven", [TimeCues()]).has_delay()


def test_user_text_never_reaches_telemetry_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    telemetry = TelemetryWriter(tmp_path / "telemetry", assistant="a", profile="p")
    service, _, _ = make_service(tmp_path, telemetry=telemetry)
    said = "SENTINEL-WORDS remind me"
    with pytest.raises(TimerAsk):
        tool(service, "set_reminder", said)(text="x", in_minutes=5)  # refused: not said
    with pytest.raises(TimerAsk):
        tool(service, "set_alarm", said)(hour=7)
    tool(service, "set_reminder", f"{said} in 5 minutes")(text="x", in_minutes=5)
    written = "".join(p.read_text() for p in (tmp_path / "telemetry").glob("*.jsonl"))
    assert "not_said" in written and '"event": "created"' in written
    assert "SENTINEL" not in written and "SENTINEL" not in caplog.text
