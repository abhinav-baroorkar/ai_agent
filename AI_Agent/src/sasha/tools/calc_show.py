"""How the calculator shows and says numbers: up to 15 significant digits, whole
numbers in full, short scientific form for speech."""

from __future__ import annotations

from decimal import Context, Decimal

SHOW_DIGITS = 15


def show(value: Decimal) -> tuple[str, bool]:
    """The value as text, and whether that text is rounded."""
    if value.is_zero():
        return "0", False
    if value == value.to_integral_value() and value.adjusted() < 34:
        whole = value.quantize(Decimal(1), context=Context(prec=40))
        return format(whole, "f"), False  # a whole number within the precision: exact
    shown = value.normalize(Context(prec=SHOW_DIGITS))
    rounded = shown != value
    exponent = shown.adjusted()
    if -7 <= exponent < 21:
        text = format(shown, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text, rounded
    digits, _, exp = format(shown, "e").partition("e")
    return f"{digits}e{int(exp):+d}", rounded


SPOKEN_DIGITS = 6  # an approximate answer is said with this many significant digits


def short(text: str) -> str:
    """An approximate number for speech: 6 significant digits ("about 37.7778")."""
    value = Decimal(text).normalize(Context(prec=SPOKEN_DIGITS))
    if abs(value.adjusted()) >= SHOW_DIGITS:  # 1.26765 times 10 to the power of 30
        digits, _, exp = format(value, "e").partition("e")
        return f"{digits}e{int(exp):+d}"
    return show(value)[0]


def say_number(text: str) -> str:
    """A shown number in words a voice reads well: "minus 3", "2 times 10 to the
    power of 30"."""
    sign = "minus " if text.startswith("-") else ""
    digits, _, exp = text.lstrip("-").partition("e")
    tail = f" times 10 to the power of {say_number(str(int(exp)))}" if exp else ""
    return f"{sign}{digits}{tail}"
