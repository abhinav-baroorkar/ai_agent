"""Timers service, time resolution, tools and hub. Fake clock, invented zone (ToyZone)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sasha.server.timers.model import TimerItem
from sasha.server.timers.service import TimerError, TimerService
from sasha.server.timers.tools import TimerAsk, TimerRefused, add_timer_tools
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter
from sasha.tools import ToolRegistry
from tests.timer_fakes import START, WEST, ZONE, FakeClock, FakeTarget, at, make_service


def handler(service: TimerService, name: str, source: str = "chat", confirm: Any = None) -> Any:
    """The tool as the model calls it (errors as TimerAsk / TimerRefused)."""
    return add_timer_tools(ToolRegistry(), service, source, confirm).get(name).handler


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, ZONE)


# --- resolving times (ask, don't guess) --------------------------------------------------


def test_set_timer_uses_whole_fields(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    result = handler(service, "set_timer")(minutes=10, label="tea")
    item = service.items[result["id"]]
    assert item.due == START + 600 and item.label == "tea" and item.created_by == "chat"
    assert result["length"] == "10 minutes" and result["ends_at"] == "Thu 27 Mar, 06:10"


@pytest.mark.parametrize(
    ("args", "reason"),
    [({}, "missing_time"), ({"seconds": 2}, "bad_value"), ({"hours": 30}, "bad_value")],
)
def test_set_timer_asks(tmp_path: Path, args: dict[str, Any], reason: str) -> None:
    service, _, _ = make_service(tmp_path)
    with pytest.raises(TimerAsk) as err:
        handler(service, "set_timer")(**args)
    assert err.value.reason == reason and not service.items


def test_registry_shows_the_question_to_the_model(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    result = add_timer_tools(ToolRegistry(), service).call("set_alarm", {"hour": 7})
    assert not result.ok and "morning or in the evening" in result.content


def test_alarm_period_rules(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)  # 06:00
    alarm = handler(service, "set_alarm")
    with pytest.raises(TimerAsk) as err:
        alarm(hour=7)  # 07:00 and 19:00 both ahead
    assert err.value.reason == "ambiguous_period"
    with pytest.raises(TimerAsk):
        alarm(hour=5)  # R1: only 17:00 is ahead today, but 05:00 tomorrow is likely
    evening = handler(service, "set_reminder")(text="x", hour=5)  # reminders: 17:00
    assert local(service.items[evening["id"]].due).hour == 17
    early = alarm(hour=5, period="am")  # passed: tomorrow, and the result says so
    assert local(service.items[early["id"]].due) == datetime(2031, 3, 28, 5, 0, tzinfo=ZONE)
    assert "tomorrow" in early["note"]
    with pytest.raises(TimerAsk):
        alarm(hour=7, day="tomorrow")  # a day without am/pm is still ambiguous
    friday = alarm(hour=7, period="am", day="friday")
    assert friday["at"] == "Fri 28 Mar, 07:00"


def test_reminder_forms(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    remind = handler(service, "set_reminder")
    with pytest.raises(TimerAsk):
        remind(text="", in_minutes=5)
    with pytest.raises(TimerAsk):
        remind(text="call back", in_minutes=5, hour=9)
    result = remind(text="call\x07 back", in_minutes=30)
    item = service.items[result["id"]]
    assert item.due == START + 1800 and item.text == "call  back"  # control char stripped


def test_repeating_alarm_comes_back_after_dismiss(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    hub.add(FakeTarget())
    result = handler(service, "set_alarm")(hour=19, minute=0, repeat="weekdays")
    item = service.items[result["id"]]
    clock.now = item.due
    service.tick()
    assert item.state == "ringing"
    service.dismiss(item.id)
    assert item.state == "scheduled" and local(item.due) == datetime(2031, 3, 28, 19, tzinfo=ZONE)


def test_daily_alarm_keeps_its_wall_time_across_summer_time(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path, FakeClock(at(2031, 3, 29, 6)))
    hub.add(FakeTarget())
    item = service.items[handler(service, "set_alarm")(hour=7, period="am", repeat="daily")["id"]]
    first = item.due
    clock.now = first
    service.tick()
    service.dismiss(item.id)
    assert local(item.due).hour == 7 and item.due - first == 23 * 3600  # one hour shorter
    assert local(item.due).tzname() == "TOYS"


def test_zone_change_moves_alarms_not_timers(tmp_path: Path) -> None:
    service, _, clock = make_service(tmp_path)
    alarm = service.items[handler(service, "set_alarm")(hour=7, period="am")["id"]]
    timer = service.items[handler(service, "set_timer")(minutes=10)["id"]]
    timer_due = timer.due
    service._rezone(clock(), WEST)
    assert datetime.fromtimestamp(alarm.due, WEST).hour == 7
    assert timer.due == timer_due


# --- ringing and delivery -----------------------------------------------------------------


def test_ring_tone_and_timeout(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    home = FakeTarget()
    hub.add(home)
    assert home.frames[0]["type"] == "timers"
    item = service.items[handler(service, "set_timer")(seconds=30, label="tea")["id"]]
    clock.advance(30)
    service.tick()
    assert item.state == "ringing" and home.rings == [(item.id, "Timer done: tea.", False)]
    ringing = next(f for f in home.frames if f["type"] == "timer_ringing")
    assert ringing == {"type": "timer_ringing", "id": item.id, "kind": "timer",
                       "created_by": "chat", "say": "Timer done: tea."}  # fmt: skip
    clock.advance(10)
    service.tick()
    assert home.rings[-1] == (item.id, "", True)  # a tone
    clock.advance(120)
    service.tick()
    assert item.state == "missed"
    stopped = home.types().index("timer_stopped")
    assert home.frames[stopped]["how"] == "timeout"
    assert home.types()[stopped + 1] == "timers"  # a snapshot always follows


def test_exactly_one_socket_speaks(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    tab, listener, home = FakeTarget("tab"), FakeTarget("listener"), FakeTarget("home")
    for target in (tab, listener, home):
        hub.add(target)
    handler(service, "set_timer")(seconds=5)
    clock.advance(5)
    service.tick()
    assert home.rings and not listener.rings and not tab.rings
    assert all("timer_ringing" in t.types() for t in (tab, listener, home))
    hub.remove(home)
    handler(service, "set_timer")(seconds=5)
    clock.advance(5)
    service.tick()
    assert listener.rings  # no Home window: the listener speaks


def test_nobody_connected_then_generic_line_only(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    item = service.items[handler(service, "set_reminder")(text="SECRET-TEXT", in_minutes=1)["id"]]
    clock.advance(60)
    service.tick()
    assert item.state == "pending"
    home = FakeTarget()
    hub.add(home)
    assert item.state == "missed"
    assert home.rings == [(item.id, "You have 1 missed reminder.", False)]
    says = [f.get("say", "") for f in home.frames]
    assert "You have 1 missed reminder." in says and not any("SECRET" in s for s in says)
    assert hub.say_text(item)  # the owner tapped it: now its text is spoken
    assert home.rings[-1][1] == "Reminder: SECRET-TEXT"


def test_late_after_suspend_is_generic(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    home = FakeTarget()
    hub.add(home)
    handler(service, "set_reminder")(text="SECRET-TEXT", in_minutes=10)
    clock.advance(2 * 3600)  # the laptop slept
    service.tick()
    assert home.rings[-1][1] == "You have 1 missed reminder."


def test_restart_recovery(tmp_path: Path) -> None:
    clock = FakeClock()
    service, _, _ = make_service(tmp_path, clock)
    timer = handler(service, "set_timer")(minutes=10)["id"]
    alarm = handler(service, "set_alarm")(hour=6, minute=10, period="am")["id"]
    daily = handler(service, "set_alarm")(hour=6, minute=5, period="am", repeat="daily")["id"]
    clock.advance(2 * 3600)  # the brain was off for two hours
    again = TimerService(service.config, JsonStore(tmp_path / "timers.json"), None, clock, ZONE)
    assert again.items[timer].state == "pending" and again.items[timer].late_s > 6000
    assert again.items[alarm].state == "missed"  # alarms only fire if at most 10 min late
    assert again.items[daily].state == "scheduled"
    assert local(again.items[daily].due) == datetime(2031, 3, 28, 6, 5, tzinfo=ZONE)


def test_ringing_at_shutdown_comes_back_pending(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    hub.add(FakeTarget())
    item_id = handler(service, "set_timer")(seconds=5)["id"]
    clock.advance(5)
    service.tick()
    again = TimerService(service.config, JsonStore(tmp_path / "timers.json"), None, clock, ZONE)
    assert again.items[item_id].state == "pending"


# --- limits, stopping, removal -------------------------------------------------------------


def test_wake_limits(tmp_path: Path) -> None:
    service, _, clock = make_service(tmp_path)
    voice = handler(service, "set_timer", "wake")
    for _ in range(5):
        voice(minutes=30)
    with pytest.raises(TimerRefused) as err:
        voice(minutes=30)
    assert err.value.reason == "limit_wake"
    clock.advance(600)
    for _ in range(5):
        voice(minutes=30)
    with pytest.raises(TimerRefused):  # 10 voice-made items active
        voice(minutes=30)
    handler(service, "set_timer")(minutes=30)  # chat is not limited by voice limits


def test_bare_snooze_works_three_times(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    hub.add(FakeTarget())
    item = service.items[handler(service, "set_timer")(seconds=5)["id"]]
    for _ in range(3):
        clock.now = item.due
        service.tick()
        service.snooze(item.id, bare=True)
    clock.now = item.due
    service.tick()
    with pytest.raises(TimerError) as err:
        service.snooze(item.id, bare=True)
    assert err.value.reason == "snooze_limit"
    service.snooze(item.id, 10)  # a tap still works
    assert item.due == clock() + 600


def test_remove_rules(tmp_path: Path) -> None:
    service, hub, clock = make_service(tmp_path)
    hub.add(FakeTarget())
    tool = handler(service, "set_timer")
    scheduled, ringing = tool(minutes=10)["id"], tool(seconds=5)["id"]
    service.remove(scheduled)
    assert service.items[scheduled].state == "done"
    clock.advance(5)
    service.tick()
    with pytest.raises(TimerError) as err:
        service.remove(ringing)
    assert err.value.reason == "use_dismiss"
    service.dismiss(ringing)
    service.remove(ringing)
    assert ringing not in service.items
    clock.advance(8 * 86400)
    service.tick()
    assert scheduled not in service.items  # ended items are kept 7 days


def test_d4_wake_turn_needs_yes_for_chat_items(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    chat_item = handler(service, "set_timer")(minutes=5, label="tea")["id"]
    asked: list[str] = []

    def say_no(action: str) -> bool:
        asked.append(action)
        return False

    with pytest.raises(TimerRefused):
        handler(service, "cancel_timer", "wake", say_no)(which="tea")
    assert asked == ["cancel the timer"] and service.items[chat_item].active
    voice_item = handler(service, "set_timer", "wake")(minutes=5, label="eggs")["id"]
    handler(service, "cancel_timer", "wake", say_no)(which="eggs")  # its own: no question
    assert not service.items[voice_item].active and len(asked) == 1
    handler(service, "set_timer", "wake")(minutes=7)
    with pytest.raises(TimerRefused):
        handler(service, "cancel_timer", "wake", say_no)(which="all")
    assert asked[-1] == "cancel all 2 timers"
    handler(service, "cancel_timer", "wake", lambda action: True)(which="all")
    assert not any(i.active for i in service.items.values())


def test_wake_list_leaves_reminder_text_out(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    handler(service, "set_reminder")(text="SECRET-TEXT", in_minutes=5)
    assert "SECRET" not in str(handler(service, "list_timers", "wake")())
    assert "SECRET" in str(handler(service, "list_timers")())


def test_cancel_by_ambiguous_label_asks(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    tool = handler(service, "set_timer")
    tool(minutes=5, label="tea")
    tool(minutes=9, label="Tea")
    with pytest.raises(TimerAsk) as err:
        handler(service, "cancel_timer")(which="tea")
    assert err.value.reason == "ambiguous_item"


# --- privacy -----------------------------------------------------------------------------------


def test_label_and_text_never_reach_telemetry_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    telemetry = TelemetryWriter(tmp_path / "telemetry", assistant="a", profile="p")
    service, hub, clock = make_service(tmp_path, telemetry=telemetry)
    hub.add(FakeTarget())
    remind = handler(service, "set_reminder")
    item = remind(text="SENTINEL-TEXT", in_minutes=1)["id"]
    timer = handler(service, "set_timer")(seconds=5, label="SENTINEL-LABEL")["id"]
    with pytest.raises(TimerAsk):
        remind(text="SENTINEL-TEXT", hour=7)
    clock.advance(60)
    service.tick()
    service.snooze(item, 5)
    service.dismiss(timer)
    written = "".join(p.read_text() for p in (tmp_path / "telemetry").glob("*.jsonl"))
    assert '"event": "created"' in written and "tool_error" in written
    assert "SENTINEL" not in written and "SENTINEL" not in caplog.text


def test_store_rejects_bad_items(tmp_path: Path) -> None:
    assert TimerItem.from_store({"id": "x", "kind": "bomb", "due": 1.0}) is None
    assert TimerItem.from_store({"id": "x", "kind": "timer", "due": 1.0, "nope": 1}) is not None
