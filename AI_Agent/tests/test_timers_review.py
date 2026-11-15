"""Fixes from the review of feat/timers-server at f30e906 (T1-T10, R1, nits).
Fake clock, invented zone (ToyZone, with made-up 2031 summer-time dates)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sasha.config import ReplyLines
from sasha.server.timers.model import TimerItem
from sasha.server.timers.ring import RingPlayer
from sasha.server.timers.service import TimerError
from sasha.server.timers.tools import TimerAsk, TimerRefused, add_timer_tools
from sasha.server.wake_truth import TruthfulReply
from sasha.tools import ToolRegistry
from tests.test_timers import handler, local
from tests.timer_fakes import WEST, ZONE, FakeClock, FakeTarget, at, make_service


def weekday_alarm(tmp: Path, clock: FakeClock | None = None) -> tuple[Any, Any, Any, TimerItem]:
    service, hub, clock = make_service(tmp, clock)
    result = handler(service, "set_alarm")(hour=7, period="am", repeat="weekdays")
    return service, hub, clock, service.items[result["id"]]


NEXT_FRIDAY_7 = datetime(2031, 3, 28, 7, 0, tzinfo=ZONE)


# --- T1: every path that ends an occurrence reschedules a repeating alarm -----------------


def test_t1_unattended_fire_then_connect_late_reschedules(tmp_path: Path) -> None:
    service, hub, clock, item = weekday_alarm(tmp_path)  # Thu 06:00 -> Thu 07:00
    clock.now = item.due
    service.tick()
    assert item.state == "pending"  # nothing connected
    clock.advance(20 * 60)  # beyond the alarm late limit when a socket comes
    hub.add(FakeTarget())
    assert item.state == "scheduled" and local(item.due) == NEXT_FRIDAY_7


def test_t1_pending_expiry_reschedules(tmp_path: Path) -> None:
    service, _, clock, item = weekday_alarm(tmp_path)
    clock.now = item.due
    service.tick()
    clock.advance(13 * 3600)  # pending longer than pending_keep_s
    service.tick()
    assert item.state == "scheduled" and local(item.due) == NEXT_FRIDAY_7


def test_t1_ring_timeout_reschedules(tmp_path: Path) -> None:
    service, hub, clock, item = weekday_alarm(tmp_path)
    hub.add(FakeTarget())
    clock.now = item.due
    service.tick()
    clock.advance(301)  # alarms ring 5 min
    service.tick()
    assert item.state == "scheduled" and local(item.due) == NEXT_FRIDAY_7


def test_t1_restart_long_after_reschedules(tmp_path: Path) -> None:
    service, _, clock, item = weekday_alarm(tmp_path)
    clock.now = item.due + 3600
    service.recover()
    assert item.state == "scheduled" and local(item.due) == NEXT_FRIDAY_7


# --- T3: alarms ring on connect; missed timers and reminders can be snoozed -------------------


def test_t3_alarm_within_late_limit_rings_on_connect(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    item = service.items[handler(service, "set_alarm")(hour=7, period="am", label="gym")["id"]]
    clock.now = item.due
    service.tick()
    clock.advance(5 * 60)  # within alarm_late_s
    home = FakeTarget()
    hub.add(home)
    assert item.state == "ringing"
    assert home.rings == [(item.id, "You have 1 missed alarm.", False)]  # D2: generic
    clock.advance(10)
    service.tick()
    assert home.rings[-1] == (item.id, "", True)  # and it keeps ringing with tones


def test_t3_snooze_missed_reminder_not_alarm_not_bare(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    remind = service.items[handler(service, "set_reminder")(text="x", in_minutes=1)["id"]]
    alarm = service.items[handler(service, "set_alarm")(hour=6, minute=1, period="am")["id"]]
    clock.advance(60)
    service.tick()
    clock.advance(11 * 60)  # alarm beyond its late limit too
    hub.add(FakeTarget())
    assert remind.state == "missed" and alarm.state == "missed"
    with pytest.raises(TimerError):
        service.snooze(remind.id, bare=True)  # not from the stop window
    with pytest.raises(TimerError):
        service.snooze(alarm.id, 10)
    again = handler(service, "snooze_timer")(which=remind.id, minutes=10)  # from a chat
    assert remind.state == "scheduled" and remind.due == clock() + 600
    assert again["count"] == 1


# --- T8: relative reminders count real seconds across summer time ---------------------------


def test_t8_relative_reminder_across_summer_time(tmp_path: Path) -> None:
    service, _, clock = make_service(tmp_path, FakeClock(at(2031, 3, 30, 1, 30)))
    item = service.items[handler(service, "set_reminder")(text="x", in_hours=2)["id"]]
    assert item.due == clock() + 2 * 3600  # 01:30 TOY -> 04:30 TOYS, two real hours
    assert local(item.due).hour == 4


# --- T2: a zone change keeps one-offs on their day, repeats on their repeat ---------------------


def test_t2_rezone_keeps_day_and_repeat(tmp_path: Path) -> None:
    service, _, clock = make_service(tmp_path)  # Thursday
    alarm = handler(service, "set_alarm")
    friday = service.items[alarm(hour=7, minute=30, period="am", day="friday")["id"]]
    weekdays = service.items[alarm(hour=7, period="am", repeat="weekdays", day="")["id"]]
    clock.advance(2 * 3600)  # 08:00 Thursday: today's 07:00 has passed
    service._rezone(clock(), WEST)
    moved = datetime.fromtimestamp(friday.due, WEST)
    assert (moved.date().isoformat(), moved.hour, moved.minute) == ("2031-03-28", 7, 30)
    assert datetime.fromtimestamp(weekdays.due, WEST).weekday() < 5


# --- T4, T6: a stopped ring doesn't play; its device is told -------------------------------------


def test_t4_ring_rechecks_and_hub_tells_the_device(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    home = FakeTarget()
    hub.add(home)
    item = service.items[handler(service, "set_timer")(seconds=5)["id"]]
    clock.advance(5)
    service.tick()
    assert hub.still_rings(item.id, home)
    service.dismiss(item.id)
    assert home.stops == [item.id] and not hub.still_rings(item.id, home)

    async def play() -> list[Any]:
        sent: list[Any] = []
        conv = SimpleNamespace(busy=False, loop=asyncio.get_running_loop(), emit=sent.append,
                               ring_started=lambda _: None)  # fmt: skip
        await RingPlayer(conv, hub).play(item.id, "", True)  # type: ignore[arg-type]
        return sent

    assert asyncio.run(play()) == []  # dismissed while it waited: no tone


# --- T9: a question back can't carry a made-up claim ---------------------------------------------


class AskedPolicy:
    def __init__(self) -> None:
        self.attempted = self.asked = True
        self.changed: list[tuple[str, str]] = []
        self.not_changed: list[tuple[str, str]] = []

    def device_label(self, domain: str, entity: str) -> str | None:
        return None

    def scene_names(self) -> list[str]:
        return []


@pytest.mark.parametrize(
    ("model", "said"),
    [
        (
            "Done, the lights are off. Morning or evening?",
            "Nothing was changed. Morning or evening?",
        ),
        ("Seven in the morning or the evening?", "Seven in the morning or the evening?"),
        (
            "Should I set it for the morning or the evening?",
            "Should I set it for the morning or the evening?",
        ),
    ],
)
def test_t9_asked_back(model: str, said: str) -> None:
    truth = TruthfulReply(ReplyLines(), AskedPolicy(), "set an alarm for seven")  # type: ignore[arg-type]
    truth.feed(model)
    assert truth.finish(model)[1] == said


# --- T10: "<name>, stop" for the item ringing here needs no yes -------------------------


def test_t10_stopping_what_rings_here_needs_no_confirm(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    hub.add(FakeTarget())
    ringing = service.items[handler(service, "set_timer")(seconds=5)["id"]]
    later = service.items[handler(service, "set_timer")(minutes=30, label="tea")["id"]]
    clock.advance(5)
    service.tick()
    asked: list[str] = []

    def no(action: str) -> bool:
        asked.append(action)
        return False

    cancel = handler(service, "cancel_timer", "wake", no)
    wake_tools = add_timer_tools(ToolRegistry(), service, "wake", no, lambda item_id: True)
    here = wake_tools.get("cancel_timer").handler
    here()  # the one ringing on this device: no question
    assert ringing.state == "done" and asked == []
    with pytest.raises(TimerRefused):
        cancel(which="tea")  # a scheduled chat-made one still asks
    assert asked == ["cancel the timer"] and later.active


# --- R1 and nits ---------------------------------------------------------------------------------


def test_r1_alarm_asks_when_the_other_reading_is_next_morning(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path, FakeClock(at(2031, 3, 27, 8)))
    with pytest.raises(TimerAsk):
        handler(service, "set_alarm")(hour=7, minute=30)
    reminder = handler(service, "set_reminder")(text="x", hour=7, minute=30)
    assert local(service.items[reminder["id"]].due).hour == 19  # reminders keep the rule


@pytest.mark.parametrize("value", ["inf", "nan", "7.9", 7.5])
def test_numbers_must_be_whole(tmp_path: Path, value: Any) -> None:
    service, _, _ = make_service(tmp_path)
    with pytest.raises(TimerAsk):
        handler(service, "set_alarm")(hour=value, period="pm")
    with pytest.raises(TimerAsk):
        handler(service, "set_timer")(minutes=value)


def test_spring_forward_repeat_keeps_the_asked_time(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path, FakeClock(at(2031, 3, 29, 6)))
    hub.add(FakeTarget())
    item = service.items[
        handler(service, "set_alarm")(hour=2, minute=30, period="am", repeat="daily")["id"]
    ]
    assert local(item.due).hour == 3 and (item.hour, item.minute) == (2, 30)  # 02:30 is a gap
    clock.now = item.due
    service.tick()
    service.dismiss(item.id)
    assert (local(item.due).day, local(item.due).hour, local(item.due).minute) == (31, 2, 30)


def test_lenient_but_unambiguous_fields(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    alarm = handler(service, "set_alarm")
    weekday = service.items[alarm(hour=6, minute=45, period="am", day="weekdays")["id"]]
    assert weekday.repeat == "weekdays"  # the bench's slip: a repeat sent as a day
    assert alarm(hour=18, minute=30, period="pm")["at"] == "Thu 27 Mar, 18:30"
    assert alarm(hour=0, period="am")["at"] == "Fri 28 Mar, 00:00"
    thirtieth = handler(service, "set_reminder")(text="x", hour=3, period="pm", day="30th")
    assert thirtieth["at"] == "Sun 30 Mar, 15:00"
    with pytest.raises(TimerAsk) as err:
        alarm(hour=6, period="am", day="2024-04-02")  # a guessed year: ask, saying today
    assert "Thursday 27 March 2031" in str(err.value)


def test_wake_list_shows_only_voice_made_labels(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    handler(service, "set_timer")(minutes=5, label="PRIVATE-LABEL")
    handler(service, "set_timer", "wake")(minutes=6, label="eggs")
    listed = str(handler(service, "list_timers", "wake")())
    assert "PRIVATE" not in listed and "eggs" in listed


def test_cancel_all_reports_partial_success(tmp_path: Path, monkeypatch: Any) -> None:
    service, _, _ = make_service(tmp_path)
    first = handler(service, "set_timer")(minutes=5)["id"]
    second = handler(service, "set_timer")(minutes=6)["id"]
    real_cancel = service.cancel

    def cancel(item_id: str, how: str = "cancelled") -> TimerItem:
        if item_id == second:
            raise TimerError("It has already ended.", "not_active")
        return real_cancel(item_id, how)

    monkeypatch.setattr(service, "cancel", cancel)
    result = handler(service, "cancel_timer")(which="all")
    assert len(result["stopped"]) == 1 and result["already_ended"] == 1
    assert service.items[first].state == "done"
