"""Days in words (fix/timer-days): "2nd of April", "April 2nd", "next Thursday". The two
bench rows that were asked back without need, and the "next <weekday>" boundary (owner
rule: within 2 days, ask "this one or the one after?"). Fake clock, invented zone."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from sasha.config_timers import TimersConfig
from sasha.server.timers.resolve import Ask, _day
from sasha.server.timers.tools import TimerAsk
from sasha.server.timers.truth import TimerTruth
from tests.test_timers import handler
from tests.timer_fakes import ZONE, FakeClock, at, make_service

THURSDAY = date(2031, 3, 27)


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, ZONE)


@pytest.mark.parametrize(
    ("spec", "want"),
    [
        ("2nd of April", date(2031, 4, 2)),
        ("2nd April", date(2031, 4, 2)),
        ("the 2nd of april", date(2031, 4, 2)),
        ("April 2nd", date(2031, 4, 2)),
        ("april the 2nd", date(2031, 4, 2)),
        ("Apr 2", date(2031, 4, 2)),
        ("27th of March", THURSDAY),  # today
        ("26th of March", date(2032, 3, 26)),  # passed: next year
        ("5th of May", date(2031, 5, 5)),
        ("this friday", date(2031, 3, 28)),
        ("friday after next", date(2031, 4, 4)),
        ("next thursday", date(2031, 4, 3)),  # said on a Thursday: a week ahead
        ("next sunday", date(2031, 3, 30)),  # 3 days ahead: the coming one
        ("next monday", date(2031, 3, 31)),
    ],
)
def test_days_in_words(spec: str, want: date) -> None:
    assert _day(spec, THURSDAY) == want


@pytest.mark.parametrize(("spec", "weekday"), [("next friday", "friday"),
                                               ("next saturday", "saturday")])  # fmt: skip
def test_next_weekday_within_two_days_asks(spec: str, weekday: str) -> None:
    with pytest.raises(Ask) as err:
        _day(spec, THURSDAY)  # Friday is 1 day ahead, Saturday 2
    assert err.value.reason == f"next_weekday:{weekday}"
    assert "2031-03-2" in str(err.value) and "after next" in str(err.value)  # both dates


@pytest.mark.parametrize("spec", ["30th of February", "2nd of Smarch", "the day after"])
def test_bad_dates_ask(spec: str) -> None:
    with pytest.raises(Ask) as err:
        _day(spec, THURSDAY)
    assert err.value.reason == "bad_day"


def test_the_two_bench_rows_resolve(tmp_path: Path) -> None:
    """Both were asked back without need at 8b5ec29 and 89a2ab1 (resolve said bad_day)."""
    service, _, clock = make_service(tmp_path)
    alarm = handler(service, "set_alarm")(hour=6, period="am", day="2nd of April")
    assert local(service.items[alarm["id"]].due) == local(at(2031, 4, 2, 6, 0))
    clock.now = at(2031, 3, 27, 10, 0)  # Thursday 10:00, as in the bench case
    reminder = handler(service, "set_reminder")(
        text="pay the rent", hour=9, period="am", day="next Thursday"
    )
    assert local(service.items[reminder["id"]].due) == local(at(2031, 4, 3, 9, 0))


def test_the_ask_back_reaches_the_server_question(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path, clock=FakeClock())
    with pytest.raises(TimerAsk) as err:
        handler(service, "set_alarm")(hour=7, period="am", day="next friday")
    calls = [{"name": "set_alarm", "ok": False, "asks": err.value.asks}]
    truth = TimerTruth(TimersConfig(), "Wake me up next Friday at 7 am.", spoken=True)
    truth.feed("Sure, next Friday.")
    assert truth.finish("Sure, next Friday.", calls) == (
        "This Friday or the one after?",
        "This Friday or the one after?",
    )


def test_a_weekday_reason_only_fills_a_real_weekday() -> None:
    truth = TimerTruth(TimersConfig(), "Set an alarm.", spoken=False)
    assert truth.question("set_alarm", "next_weekday:friday") == "This Friday or the one after?"
    assert truth.question("set_alarm", "next_weekday:{x}") == TimersConfig().ask_generic
