"""`calculate` and `convert`: exact answers, hostile input, the unit table, wiring."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest

from sasha.bots import MAIN_ID, VOICE_ID, parse_fields
from sasha.config import Settings
from sasha.prompting import build_prompt, prompt_lines
from sasha.server.spoken import to_spoken
from sasha.server.wake_policy import READ_ONLY_TOOLS
from sasha.tools import Tool, ToolRegistry
from sasha.tools import calc as calc_module
from sasha.tools.calc import MAX_CHARS, MAX_DEPTH, CalcError, calculate
from sasha.tools.numbers import CALC_TOOL, CONVERT_TOOL, number_tools
from sasha.tools.units import UnitError, convert, load_table, parse_table
from tests.server_fakes import Harness

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.mark.parametrize(
    ("expression", "result", "approximate"),
    [
        ("2*3", "6", False),
        ("2 x 3", "6", False),
        ("2 \u00d7 3 \u00f7 4", "1.5", False),
        ("7 \u2212 10", "-3", False),
        ("1,000 + 2.5", "1002.5", False),
        ("0.1 + 0.2", "0.3", False),  # exact decimals, not floats
        ("(1 + 2) * (3 + 4)", "21", False),
        ("2^10", "1024", False),
        ("2**-1", "0.5", False),
        ("2^3^2", "512", False),  # right-associative
        ("-2^2", "-4", False),
        ("5²", "25", False),
        ("20% of 150", "30", False),
        ("150 + 10%", "165", False),  # as a calculator does
        ("150 - 10%", "135", False),
        ("12.5%", "0.125", False),
        ("sqrt(16)", "4", False),
        ("sqrt(2)", "1.4142135623731", True),
        ("cbrt(-27)", "-3", False),
        ("1/3", "0.333333333333333", True),
        ("sin(30)", "0.5", False),
        ("cos(60)", "0.5", False),
        ("tan(45)", "1", False),
        ("log(1000)", "3", False),
        ("ln(1)", "0", False),
        ("round(2.5)", "3", False),
        ("floor(-2.5)", "-3", False),
        ("ceil(2.1)", "3", False),
        ("abs(-4)", "4", False),
        ("1.5e3", "1500", False),
        ("2^100", "1267650600228229401496703205376", False),
        ("2^200", "1.60693804425899e+60", True),
        # review S1, S3 and nits
        ("2\u00b3\u00b2", "4294967296", False),
        ("sin(180)", "0", False),
        ("cos(270)", "0", False),
        ("sin(3.8)", "0.0662739004000001", True),
        ("sin(1e9)", "-0.984807753012208", True),
        ("cbrt(1000)", "10", False),
        ("cbrt(1073741824)", "1024", False),
        ("50% + 50%", "1", False),
        ("9^0.5", "3", False),
        # review N1b: + and - are exact at any width
        ("(1e34+1)-1e34", "1", False),
        ("1e40+1-1e40", "1", False),
        ("1e90+1-1e90", "1", False),
        ("1e90+1", "1e+90", True),
        ("2/3*3", "2", True),  # a lossy division: never called exact (N1b)
    ],
)
def test_calculate(expression: str, result: str, approximate: bool) -> None:
    out = calculate(expression)
    assert (out["result"], out["approximate"]) == (result, approximate)


def test_said_as_reads_naturally() -> None:
    assert calculate("2*3")["said_as"] == "2 times 3 is 6"
    assert calculate("20% of 150")["said_as"] == "20 percent of 150 is 30"
    assert calculate("10 / 4")["said_as"] == "10 divided by 4 is 2.5"
    assert calculate("sqrt(2)")["said_as"] == "the square root of 2 is about 1.41421"
    assert calculate("3 - 5")["said_as"] == "3 minus 5 is minus 2"
    assert calculate("2^200")["said_as"].endswith("about 1.60694 times 10 to the power of 60")
    assert calculate("pi * 2")["said_as"].startswith("pi times 2 is about 6.28319")
    # a whole number too long to say digit by digit
    assert calculate("2^100")["said_as"].endswith("about 1.26765 times 10 to the power of 30")
    assert calculate("2^100")["approximate"] is False


def test_trig_exactness_follows_niven() -> None:
    """Review S3: only 0, 1/2 and 1 (and tan 0, 1) are exact; nothing else."""
    exact = {"0", "0.5", "-0.5", "1", "-1"}
    for tenths in range(0, 3600, 7):
        for name in ("sin", "cos", "tan"):
            if name == "tan" and tenths % 1800 == 900:
                continue
            out = calculate(f"{name}({tenths / 10})")
            if not out["approximate"]:
                assert out["result"] in exact, (name, tenths, out)
    assert calculate("sin(1e-10)")["result"] != "0"  # never snapped to zero


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("1" * 10_000, "Too long"),
        ("(" * 5_000 + "1" + ")" * 5_000, "Too long"),
        ("(" * 40 + "1" + ")" * 40, "nested"),
        ("-" * 120 + "1", "nested"),
        ("sqrt(" * 40 + "4" + ")" * 40, "nested"),
        ("9^9^9", "power is too large"),
        ("2^100000", "power is too large"),
        ("10^10000", "too large"),
        ("1e9999 * 1e9999", "too large"),
        ("exp(100000)", "too large"),
        ("1/0", "zero"),
        ("0^-1", "Zero"),
        ("(-8)^(1/3)", "No real result"),
        ("sqrt(-1)", "negative"),
        ("ln(0)", "positive"),
        ("tan(90)", "undefined"),
        ("__import__('os').system('true')", "Can't read"),
        ("open('x')", "Unknown word"),
        ("2 +", "ends too early"),
        ("(2", "ends too early"),
        ("2 3", "Unexpected"),
        ("", "Nothing"),
        ("1e99999", "Number too large"),
        # review N1, N2
        ("12345678901234567890123456789012345-12345678901234567890123456789012340", "34 digits"),
        ("1/3*3-1", "loses too many digits"),
        ("1e-9999/1e9999", "too small"),
        ("exp(-23100)", "too small"),
        ("1e-5000^2", "too small"),
        ("0x10", "Hex"),  # S2
        ("round(1e40)", "Too large to round"),
    ],
)
def test_hostile_input_is_refused_quickly(expression: str, message: str) -> None:
    start = time.perf_counter()
    with pytest.raises(CalcError, match=message):
        calculate(expression)
    assert time.perf_counter() - start < 0.05


def test_the_limits_and_no_eval() -> None:
    assert MAX_CHARS <= 300 and MAX_DEPTH <= 30
    source = Path(calc_module.__file__).read_text()
    for banned in (r"(?<![\w.])eval\(", r"(?<![\w.])exec\(", r"import ast", r"(?<![\w.])compile\("):
        assert not re.search(banned, source), banned
    start = time.perf_counter()
    assert calculate("1e9999 - 1e-9999")["approximate"]  # the widest exact sum
    assert time.perf_counter() - start < 0.05
    start = time.perf_counter()
    for _ in range(200):
        calculate("((1.000001^9999 + sqrt(2)) * 3.3 / 7) ^ 3")
    assert time.perf_counter() - start < 1.0


# --- convert -------------------------------------------------------------------------
@pytest.fixture(scope="module")
def table() -> Any:
    return load_table(CONFIG)


@pytest.mark.parametrize(
    ("value", "src", "dst", "result"),
    [
        (1, "mile", "km", "1.609344"),
        (37, "celsius", "fahrenheit", "98.6"),
        (0, "C", "F", "32"),
        (-40, "F", "C", "-40"),
        (0, "K", "C", "-273.15"),
        (3, "tbsp", "tsp", "9"),
        (1, "cup", "ml", "236.5882365"),
        (2, "c", "tsp", "96"),  # "c": cups, because teaspoons are a volume
        (250, "ml", "metric cup", "1"),
        (1, "GiB", "MiB", "1024"),
        (1, "Mb", "MB", "0.125"),  # case matters for data sizes
        (1, "KB", "bytes", "1000"),
        (1, "Kb", "bytes", "125"),  # review S4: Kb is a kilobit, like Mb
        (60, "mph", "km/h", "96.56064"),
        (1, "day", "minutes", "1440"),
        (5, "kg", "lb", "11.0231131092439"),
        ("1,000", "m", "km", "1"),
    ],
)
def test_convert(table: Any, value: Any, src: str, dst: str, result: str) -> None:
    assert convert(table, value, src, dst)["result"] == result


def test_convert_words_and_refusals(table: Any) -> None:
    assert convert(table, 1, "mile", "km")["said_as"] == "1 mile is 1.609344 kilometres"
    hot = convert(table, 100, "F", "C")
    assert hot["approximate"] and hot["said_as"].endswith("about 37.7778 degrees Celsius")
    for args, message in (
        ((1, "m", "s"), "Can't convert"),
        ((1, "mb", "kB"), "Which one"),
        ((1, "furlong", "m"), "Unknown unit"),
        ((-500, "C", "K"), "absolute zero"),
        (("1+1", "m", "km"), "plain number"),
        (("9" * 100, "m", "km"), "plain number"),
        ((1, "m" * 100, "km"), "unit name"),
    ):
        with pytest.raises(UnitError, match=message):
            convert(table, *args)


def test_every_unit_is_reachable_and_round_trips(table: Any) -> None:
    for unit in table.units:
        name = unit.key.replace("_", " ")
        base = next(u for u in table.units if u.kind == unit.kind)
        assert convert(table, 7, name, name)["result"] == "7"
        there = convert(table, 7, name, base.key)["result"]
        back = float(convert(table, there, base.key, name)["result"])
        assert abs(back - 7) < 1e-9, unit.key


def test_the_table_is_checked() -> None:
    with pytest.raises(ValueError, match="needs"):
        parse_table({"length": {"metre": [1, 0]}})
    with pytest.raises(ValueError, match="positive"):
        parse_table({"length": {"metre": [0, 0, "m", "m"]}})


# --- wiring --------------------------------------------------------------------------
def test_both_tools_are_read_only_and_marked() -> None:
    tools = {t.name: t for t in number_tools(CONFIG)}
    assert set(tools) == {CALC_TOOL, CONVERT_TOOL}
    assert all(t.read_only and not t.taints for t in tools.values())
    assert Tool("x", "x", {}, lambda: None).read_only is False  # new tools: changing
    assert {CALC_TOOL, CONVERT_TOOL} <= set(READ_ONLY_TOOLS)  # wake turns may use them
    assert ToolRegistry(tools.values()).call(CALC_TOOL, {"expression": "1/0"}).ok is False


def test_the_built_in_bots_get_them_and_others_dont(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path)
    for bot_id in (MAIN_ID, VOICE_ID):
        assistant, _ = h.deps.make_assistant(h.bots.get(bot_id))
        assert CALC_TOOL in assistant.tools and CONVERT_TOOL in assistant.tools
        assert prompt_lines(settings).abilities[CALC_TOOL] in assistant.system_prompt
    other = h.bots.create(parse_fields({"name": "Plain"}))
    assistant, _ = h.deps.make_assistant(other)
    assert CALC_TOOL not in assistant.tools
    assert prompt_lines(settings).abilities[CALC_TOOL] not in assistant.system_prompt


def test_abilities_only_when_present(settings: Settings) -> None:
    lines = prompt_lines(settings)
    assert lines.abilities[CONVERT_TOOL] in build_prompt("b", [CONVERT_TOOL], lines)
    assert lines.abilities[CONVERT_TOOL] not in build_prompt("b", [], lines)


def test_spoken_text_reads_the_times_sign() -> None:
    assert to_spoken("2 \u00d7 3 is 6.") == "2 times 3 is 6."
    assert to_spoken("2*3 is 6.") == "2 times 3 is 6."
