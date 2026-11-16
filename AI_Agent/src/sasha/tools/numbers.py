"""`calculate` and `convert`: read-only tools for the built-in bots (and wake turns).

The model is bad at arithmetic and unit factors; these give exact answers, plus a
`said_as` sentence in words ("2 times 3 is 6") for a spoken reply. English words for
now (Phase 1 speaks English).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sasha.tools.base import Tool, ToolError
from sasha.tools.calc import CalcError, calculate
from sasha.tools.units import UnitError, convert, load_table

CALC_TOOL = "calculate"
CONVERT_TOOL = "convert"
NUMBER_TOOLS = (CALC_TOOL, CONVERT_TOOL)


def number_tools(config_dir: Path) -> list[Tool]:
    def calc(expression: str) -> dict[str, Any]:
        try:
            return calculate(expression)
        except CalcError as e:
            raise ToolError(str(e)) from None

    def conv(value: Any, from_unit: str, to_unit: str) -> dict[str, Any]:
        try:
            return convert(load_table(config_dir), value, from_unit, to_unit)
        except UnitError as e:
            raise ToolError(str(e)) from None

    text = {"type": "string"}
    return [
        Tool(
            CALC_TOOL,
            "Work out arithmetic exactly: + - * / ^, brackets, percent ('20% of 150', "
            "'150 + 10%'), sqrt, cbrt, abs, round, floor, ceil, ln, log, exp, and sin, "
            "cos, tan in degrees. Use it for any sum instead of working it out yourself. "
            "Returns the result and `said_as`, the answer in words for speaking.",
            {"type": "object", "properties": {"expression": text}, "required": ["expression"]},
            calc,
            read_only=True,
        ),
        Tool(
            CONVERT_TOOL,
            "Convert a value between units of length, mass, volume (including cups, "
            "tbsp, tsp), temperature, speed, time or data size, e.g. value=5, "
            "from_unit='miles', to_unit='km'. Returns the result and `said_as`.",
            {
                "type": "object",
                "properties": {
                    "value": {"type": "number"},
                    "from_unit": text,
                    "to_unit": text,
                },
                "required": ["value", "from_unit", "to_unit"],
            },
            conv,
            read_only=True,
        ),
    ]
