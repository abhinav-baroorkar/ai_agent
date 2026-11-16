"""Unit conversion from one table (`config/units.yaml`), exact with fractions.

A unit is looked up by its key, its singular or plural name, or an alias: first with
case (so "MB" and "Mb" differ), then without case if that names one unit only. When a
name fits several kinds ("c": cup or Celsius), the kind the other unit has decides.
Temperatures have offsets; nothing goes below absolute zero.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from sasha.tools.calc import CalcError, say_number, short, show

MAX_VALUE_CHARS = 40
_VALUE = re.compile(r"^[-+]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?([eE][-+]?\d{1,3})?$")


class UnitError(ValueError):
    """A conversion that can't be done; the message is safe to show."""


@dataclass(frozen=True)
class Unit:
    key: str
    kind: str
    factor: Fraction
    offset: Fraction
    singular: str
    plural: str


@dataclass(frozen=True)
class Table:
    units: tuple[Unit, ...]
    exact: dict[str, tuple[Unit, ...]]  # name as written -> units
    folded: dict[str, tuple[Unit, ...]]  # lowercase -> units


def _fraction(value: Any) -> Fraction:
    text = str(value).strip()
    if "/" in text:
        top, bottom = text.split("/", 1)
        return Fraction(Fraction(top.strip()), Fraction(bottom.strip()))
    return Fraction(text)


def _norm(name: str) -> str:
    return " ".join(str(name).replace("_", " ").split()).strip(" .")


def parse_table(data: dict[str, Any]) -> Table:
    units: list[Unit] = []
    exact: dict[str, list[Unit]] = {}
    folded: dict[str, list[Unit]] = {}
    for kind, entries in data.items():
        for key, row in (entries or {}).items():
            if not isinstance(row, list) or len(row) < 4:
                shape = "[factor, offset, singular, plural, aliases...]"
                raise ValueError(f"units.yaml: {kind}.{key} needs {shape}")
            unit = Unit(key, kind, _fraction(row[0]), _fraction(row[1]), str(row[2]), str(row[3]))
            if unit.factor <= 0:
                raise ValueError(f"units.yaml: {kind}.{key} factor must be positive")
            units.append(unit)
            for name in {_norm(key), *(_norm(n) for n in row[2:])}:
                exact.setdefault(name, []).append(unit)
                if unit not in folded.setdefault(name.lower(), []):
                    folded[name.lower()].append(unit)
    return Table(
        tuple(units),
        {k: tuple(v) for k, v in exact.items()},
        {k: tuple(v) for k, v in folded.items()},
    )


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> Table:
    return parse_table(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def load_table(config_dir: Path) -> Table:
    path = config_dir / "units.yaml"
    return _load(str(path), path.stat().st_mtime)


def _candidates(table: Table, name: str) -> tuple[Unit, ...]:
    text = _norm(name)
    if not text or len(text) > 40:
        raise UnitError("Give a unit name, e.g. 'km' or 'cups'")
    found = table.exact.get(text) or table.folded.get(text.lower())
    if not found:
        raise UnitError(f"Unknown unit {text!r}")
    return found


def _pick(table: Table, src: str, dst: str) -> tuple[Unit, Unit]:
    pairs = [(a, b) for a in _candidates(table, src) for b in _candidates(table, dst)
             if a.kind == b.kind]  # fmt: skip
    if not pairs:
        a_kinds = "/".join(sorted({u.kind for u in _candidates(table, src)}))
        b_kinds = "/".join(sorted({u.kind for u in _candidates(table, dst)}))
        raise UnitError(f"Can't convert {a_kinds} to {b_kinds}")
    if len({(a.key, b.key) for a, b in pairs}) > 1:
        names = ", ".join(sorted({f"{a.plural} to {b.plural}" for a, b in pairs}))
        raise UnitError(f"Which one: {names}? Say the full unit name")
    return pairs[0]


def _value(value: Any) -> Fraction:
    text = str(value).strip().replace(" ", "")
    if len(text) > MAX_VALUE_CHARS or not text or not _VALUE.match(text):
        raise UnitError("The value must be a plain number, e.g. 3.5")
    try:
        return Fraction(Decimal(text.replace(",", "")))
    except (ArithmeticError, ValueError):
        raise UnitError("The value must be a plain number, e.g. 3.5") from None


def convert(table: Table, value: Any, from_unit: str, to_unit: str) -> dict[str, Any]:
    amount = _value(value)
    src, dst = _pick(table, from_unit, to_unit)
    base = amount * src.factor + src.offset
    if src.kind == "temperature" and base < 0:
        raise UnitError("That is below absolute zero")
    exact = (base - dst.offset) / dst.factor
    with localcontext(Context(prec=34)):
        result = Decimal(exact.numerator) / Decimal(exact.denominator)
    try:
        shown, rounded = show(result)
        given, _ = show(Decimal(amount.numerator) / Decimal(amount.denominator))
    except CalcError as e:  # pragma: no cover - show doesn't raise today
        raise UnitError(str(e)) from None
    src_name = src.singular if amount == 1 else src.plural
    dst_name = dst.singular if exact == 1 else dst.plural
    about = "about " if rounded else ""
    return {
        "result": shown,
        "unit": dst.plural,
        "approximate": rounded,
        "said_as": (
            f"{say_number(given)} {src_name} is {about}"
            f"{say_number(short(shown) if rounded else shown)} {dst_name}"
        ),
    }
