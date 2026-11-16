"""The timer phrasing bench (bench/timer_phrases.py + .yaml) is well formed, and its
verdicts follow the pass bar: an item at a time not wanted is a silent wrong time."""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"
DATA = yaml.safe_load((BENCH_DIR / "timer_phrases.yaml").read_text(encoding="utf-8"))


def load_bench() -> ModuleType:
    spec = importlib.util.spec_from_file_location("timer_phrases", BENCH_DIR / "timer_phrases.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BENCH = load_bench()


def test_about_forty_cases_of_each_kind() -> None:
    kinds = [c["kind"] for c in DATA["cases"]]
    assert len(kinds) >= 40 and {"timer", "alarm", "reminder"} <= set(kinds)
    assert sum(1 for c in DATA["cases"] if "ask" in c["want"]) >= 8  # ambiguity is tested


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: str(c["say"]))
def test_case_is_well_formed(case: dict[str, Any]) -> None:
    assert case["kind"] in ("timer", "alarm", "reminder") and case["want"]
    now = BENCH.parse_local(str(case.get("now") or DATA["start"]))
    if case["want"] == ["talk"]:  # mention-only or status: nothing should be made
        return
    for want in case["want"]:
        if want != "ask":
            assert BENCH.parse_local(str(want)) > now  # always in the future


def item(kind: str, due: Any) -> tuple[list[Any], list[Any]]:
    return [SimpleNamespace(kind=kind)], [due]


def test_verdicts() -> None:
    case = {"kind": "alarm", "want": ["2031-03-27 07:30"]}
    at = BENCH.parse_local("2031-03-27 07:30")
    assert BENCH.verdict(case, *item("alarm", at), [], "") == "right"
    assert BENCH.verdict(case, *item("reminder", at), [], "") == "wrong_kind"
    assert BENCH.verdict(case, *item("alarm", at + timedelta(hours=12)), [], "") == "wrong"
    two = [SimpleNamespace(kind="alarm")] * 2
    assert BENCH.verdict(case, two, [at, at], [], "") == "wrong"  # both readings set
    assert (
        BENCH.verdict(case, [], [], [{"tool": "set_alarm", "asked": "x"}], "") == "asked_unneeded"
    )
    assert BENCH.verdict({"kind": "alarm", "want": ["ask"]}, [], [], [], "Morning?") == "asked"
    assert BENCH.verdict(case, [], [], [], "Okay.") == "none"
    timer = {"kind": "timer", "want": ["2031-03-27 06:10:00"]}
    off_by_30s = BENCH.parse_local("2031-03-27 06:10:30")
    assert BENCH.verdict(timer, *item("timer", off_by_30s), [], "") == "wrong"  # to the second


def test_run_case_scores_the_server_line(settings: Any, tmp_path: Path) -> None:
    """Offline (a fake model): a false claim gets the server's line, so none is uncaught."""
    from tests.server_fakes import FakeLLM, resp

    paths = settings.paths.model_copy(update={"data_dir": tmp_path})
    settings = settings.model_copy(update={"paths": paths})
    case = {"say": "Wake me up at 7.", "kind": "alarm", "want": ["ask"]}
    row = BENCH.run_case(settings, FakeLLM("m", [resp("Done, see you at 7.")]), case, DATA["start"])
    assert row["server_line"] and not row["uncaught_claim"] and row["verdict"] == "none"
    weather = {"say": "What's the weather?", "kind": "alarm", "want": ["ask"]}
    honest = BENCH.run_case(settings, FakeLLM("m", [resp("Sunny.")]), weather, DATA["start"])
    assert not honest["server_line"] and not honest["uncaught_claim"]


def test_uncaught_claims_and_spoken_claims() -> None:
    from sasha.config_timers import TimersConfig

    cfg = TimersConfig()
    assert BENCH.uncaught(True, False, False)
    assert not BENCH.uncaught(True, True, False)  # made
    assert not BENCH.uncaught(True, False, True)  # the line was added
    got = BENCH.judge(cfg, "Set an alarm for 7.", [], "Done. Anything else?")
    assert not got["spoken_claim"] and got["replaced"]  # the server's line was said
    talk = BENCH.judge(cfg, "Thanks for the reminder.", [], "You're welcome!")
    assert not talk["replaced"] and talk["spoken"] == "You're welcome!"


def test_the_probes_pass() -> None:
    """The fixed replies in the YAML (Q1, Q2, the status line), judged without a model."""
    from sasha.config_timers import TimersConfig

    failed = [p for p in BENCH.probes(TimersConfig(), DATA["probes"]) if not p["ok"]]
    assert not failed


def test_judge_sees_the_tools_ask() -> None:
    from sasha.config_timers import TimersConfig

    cfg = TimersConfig()
    calls = [{"tool": "set_alarm", "args": {"hour": 7}, "asked": "ambiguous_period"}]
    got = BENCH.judge(cfg, "Wake me up at 7.", calls, "I need to know: 7 AM or 7 PM.")
    assert got["server_question"] and got["spoken"] == "Morning or evening?"
    assert got["shown"].endswith("Morning or evening?")


def test_a_report_or_an_earlier_item_is_no_false_claim() -> None:
    """After list_timers the reply is a report (truth.reported); in a two-turn case an
    item made in the first turn makes "your alarm is set" true."""
    from sasha.config_timers import TimersConfig

    cfg = TimersConfig()
    listed = [{"tool": "list_timers", "args": {}, "result": {"items": []}}]
    report = BENCH.judge(cfg, "Is my alarm set?", listed, "You don't have any alarms set.")
    assert not report["spoken_claim"] and not report["uncaught_claim"]
    earlier = BENCH.judge(cfg, "Is my alarm set?", [], "Your alarm is set for 7.", existing=True)
    assert not earlier["spoken_claim"] and not earlier["uncaught_claim"]
    cleaned = BENCH.judge(cfg, "Tell me about timers.", [], "I can help:\n- Set a timer\n")
    assert "- " not in cleaned["spoken"]  # the bench reports speech after the cleaner
