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


def test_a_question_back_is_no_uncaught_claim() -> None:
    """The server leaves a question back alone, so the bench must not count it either."""
    asking = "I'll set an alarm for 3 o'clock. Is that AM or PM?"
    assert not BENCH.uncaught(True, False, False, asking)
    assert BENCH.uncaught(True, False, False, "Alarm set for 3.")
    assert not BENCH.uncaught(True, True, False, "Alarm set for 3.")  # made
    assert not BENCH.uncaught(True, False, True, "Alarm set for 3.")  # the line was added
    old = {"uncaught_claim": True, "server_line": False, "reply": asking}  # an older file
    assert not BENCH.rescored(old)
    assert BENCH.rescored({**old, "reply": "Alarm set for 3."})
