"""A safe calculator: its own tokenizer and recursive-descent parser over `Decimal`.

Never `eval`, `exec` or `ast`. What it reads: numbers (`1,000.5`, `1.2e3`), + - and the times and
divide signs, their ASCII forms (`*`, `/`, `x` between numbers), `^` / `**` and ² ³, parentheses,
`%` (`20% of 150`; `150 + 10%` is 165, as a calculator does), `pi`, and one-argument
functions: sqrt, cbrt, abs, round, floor, ceil, ln, log (base 10), exp, and sin, cos,
tan in degrees. Bounds, so nothing can hang or blow up memory: `MAX_CHARS`,
`MAX_TOKENS`, `MAX_DEPTH` of nesting, `MAX_EXPONENT`, and a decimal context whose
exponent range ends at 10^±9999 (bigger is refused as "too large"). Results are exact
where the arithmetic is (34 significant digits inside), then shown with up to 15.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Subnormal,
    Underflow,
    getcontext,
    localcontext,
)
from typing import Any

from sasha.tools.calc_trig import TrigError, trig

MAX_CHARS = 300
MAX_TOKENS = 150
MAX_DEPTH = 30
MAX_EXPONENT = 10_000
SHOW_DIGITS = 15
PRECISION = 34  # digits inside; a number written with more is refused (review N1)
CHECK_PRECISION = 80  # the same sum again: if the shown digits differ, it's refused
_TRAPS = [Overflow, DivisionByZero, InvalidOperation, Underflow, Subnormal]  # N2


def _context(prec: int) -> Context:
    return Context(prec=prec, Emax=9_999, Emin=-9_999, traps=list(_TRAPS))


_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d*)?(?:[eE][+-]?\d{1,9})?|\.\d+)"
    r"|(?P<sup>[\u2070\u00b9\u00b2\u00b3\u2074-\u2079]+)"
    r"|(?P<name>[A-Za-z]+)"
    r"|(?P<op>\*\*|[-+*/^%()\u00d7\u00f7\u2212\u00b7])"
    r")"
)
FUNCTIONS = ("sqrt", "cbrt", "abs", "round", "floor", "ceil", "ln", "log", "exp",
             "sin", "cos", "tan")  # fmt: skip
# times, middle dot, divide, minus sign: as their ASCII operators
_SIGNS = {"\u00d7": ["*"], "\u00b7": ["*"], "\u00f7": ["/"], "\u2212": ["-"], "**": ["^"]}
# A run of superscript digits is one exponent: 2\u00b3\u00b2 is 2^32 (review S1)
_SUPERSCRIPT = str.maketrans(
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079", "0123456789"
)
WORDS = {"x": "*", "of": "*", "times": "*", "percent": "%", "plus": "+", "minus": "-"}
OP_WORDS = {"+": "plus", "-": "minus", "*": "times", "/": "divided by", "^": "to the power of"}

FUNCTION_WORDS = {"sqrt": "the square root of", "cbrt": "the cube root of",
                  "ln": "the natural log of", "log": "the log of", "exp": "e to the power of",
                  "abs": "the absolute value of", "round": "rounded", "floor": "rounded down",
                  "ceil": "rounded up"}  # fmt: skip


class CalcError(ValueError):
    """The expression can't be worked out; the message says why (safe to show)."""


@dataclass(frozen=True)
class Node:
    kind: str  # num, neg, pct, bin, call
    value: Decimal | None = None
    op: str = ""
    args: tuple[Node, ...] = ()


def tokens(text: str) -> list[str]:
    if len(text) > MAX_CHARS:
        raise CalcError(f"Too long: at most {MAX_CHARS} characters")
    out: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if m is None or m.end() == pos:
            raise CalcError(f"Can't read {text[pos : pos + 10].strip()!r}")
        pos = m.end()
        if m.group("num"):
            out.append(m.group("num").replace(",", ""))
        elif m.group("sup"):
            out += ["^", m.group("sup").translate(_SUPERSCRIPT)]
        elif m.group("name"):
            word = m.group("name").lower()
            if word.startswith("x") and out[-1:] == ["0"] and m.start("name") == m.start():
                raise CalcError("Hex numbers aren't supported")  # 0x10 (review S2)
            if word not in FUNCTIONS and word not in WORDS and word != "pi":
                raise CalcError(f"Unknown word {word!r}")
            out.append(WORDS.get(word, word))
        else:
            op = m.group("op")
            out += _SIGNS.get(op, [op])
        if len(out) > MAX_TOKENS:
            raise CalcError(f"Too long: at most {MAX_TOKENS} parts")
    if not out:
        raise CalcError("Nothing to work out")
    return out


class _Parser:
    def __init__(self, items: list[str]) -> None:
        self.items = items
        self.i = 0
        self.depth = 0

    def peek(self) -> str | None:
        return self.items[self.i] if self.i < len(self.items) else None

    def take(self) -> str:
        item = self.peek()
        if item is None:
            raise CalcError("The expression ends too early")
        self.i += 1
        return item

    def deeper(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise CalcError(f"Too deeply nested (at most {MAX_DEPTH})")

    def expr(self) -> Node:
        node = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            node = Node("bin", op=op, args=(node, self.term()))
        return node

    def term(self) -> Node:
        node = self.unary()
        while self.peek() in ("*", "/"):
            op = self.take()
            node = Node("bin", op=op, args=(node, self.unary()))
        return node

    def unary(self) -> Node:
        if self.peek() in ("-", "+"):
            self.deeper()
            sign = self.take()
            inner = self.unary()
            self.depth -= 1
            return Node("neg", args=(inner,)) if sign == "-" else inner
        return self.power()

    def power(self) -> Node:
        base = self.postfix()
        if self.peek() == "^":
            self.take()
            self.deeper()
            exponent = self.unary()  # right-associative; 2^-1 works
            self.depth -= 1
            return Node("bin", op="^", args=(base, exponent))
        return base

    def postfix(self) -> Node:
        node = self.primary()
        while self.peek() == "%":
            self.take()
            node = Node("pct", args=(node,))
        return node

    def primary(self) -> Node:
        item = self.take()
        if item == "(":
            self.deeper()
            node = self.expr()
            if self.take() != ")":
                raise CalcError("A bracket isn't closed")
            self.depth -= 1
            return node
        if item == "pi":
            return Node("num", value=Decimal("3.141592653589793238462643383279503"), op="pi")
        if item in FUNCTIONS:
            if self.peek() != "(":
                raise CalcError(f"{item} needs brackets, e.g. {item}(2)")
            return Node("call", op=item, args=(self.primary(),))
        if item[0].isdigit() or item[0] == ".":
            _, _, exp = item.lower().partition("e")
            if exp and abs(int(exp)) > MAX_EXPONENT:
                raise CalcError("Number too large")
            value = Decimal(item)
            if len(value.normalize(Context(prec=1000)).as_tuple().digits) > PRECISION:
                raise CalcError(f"Numbers can have at most {PRECISION} digits")  # N1
            return Node("num", value=value)
        raise CalcError(f"Unexpected {item!r}")


def parse(text: str) -> Node:
    parser = _Parser(tokens(text))
    node = parser.expr()
    if parser.peek() is not None:
        raise CalcError(f"Unexpected {parser.peek()!r}")
    return node


def _cbrt(x: Decimal) -> Decimal:
    """The cube root; exact when a rounded root cubes back to x (cbrt(1000) is 10)."""
    ctx = getcontext()
    inexact_before = ctx.flags[Inexact]
    root = (abs(x) ** (Decimal(1) / 3)).copy_sign(x)
    near = root.normalize(Context(prec=ctx.prec - 2))
    with localcontext(Context(prec=ctx.prec * 3 + 10, traps=[])):
        exact = near * near * near == x
    if exact:
        ctx.flags[Inexact] = inexact_before  # this step lost nothing
        return near
    return root


def _call(name: str, x: Decimal, approx: list[bool]) -> Decimal:
    if name in ("sin", "cos", "tan"):
        try:
            value, inexact = trig(name, x)
        except TrigError as e:
            raise CalcError(str(e)) from None
        approx[0] = approx[0] or inexact
        return value
    if name == "sqrt":
        if x < 0:
            raise CalcError("No real square root of a negative number")
        return x.sqrt()
    if name == "cbrt":
        return _cbrt(x)
    if name in ("ln", "log"):
        if x <= 0:
            raise CalcError(f"{name} needs a positive number")
        return x.ln() if name == "ln" else x.log10()
    if name == "exp":
        return x.exp()
    if name == "abs":
        return abs(x)
    rounding = {"round": "ROUND_HALF_UP", "floor": "ROUND_FLOOR", "ceil": "ROUND_CEILING"}
    if x.adjusted() >= getcontext().prec:
        raise CalcError(f"Too large to {name}")
    return x.quantize(Decimal(1), rounding=rounding[name])


def _eval(node: Node, approx: list[bool]) -> Decimal:
    if node.kind == "num":
        assert node.value is not None
        return +node.value  # rounded to the context
    if node.kind == "neg":
        return -_eval(node.args[0], approx)
    if node.kind == "pct":
        return _eval(node.args[0], approx) / 100
    if node.kind == "call":
        return _call(node.op, _eval(node.args[0], approx), approx)
    left_node, right_node = node.args
    left = _eval(left_node, approx)
    if node.op in "+-" and right_node.kind == "pct" and left_node.kind != "pct":  # 150+10%
        share = left * _eval(right_node, approx)
        return left + share if node.op == "+" else left - share
    right = _eval(right_node, approx)
    if node.op == "+":
        return left + right
    if node.op == "-":
        return left - right
    if node.op == "*":
        return left * right
    if node.op == "/":
        return left / right
    if abs(right) > MAX_EXPONENT:
        raise CalcError(f"The power is too large (at most {MAX_EXPONENT})")
    if left == 0 and right < 0:
        raise CalcError("Zero can't be raised to a negative power")
    if left < 0 and right != right.to_integral_value():
        raise CalcError("No real result for a negative number to a fractional power")
    return left**right


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


def spoken(node: Node) -> str:
    """The expression in words ("2 times 3"), for a spoken answer."""
    if node.kind == "num":
        assert node.value is not None
        return "pi" if node.op == "pi" else say_number(show(node.value)[0])
    if node.kind == "neg":
        return f"minus {spoken(node.args[0])}"
    if node.kind == "pct":
        return f"{spoken(node.args[0])} percent"
    if node.kind == "call":
        names = FUNCTION_WORDS
        inner = spoken(node.args[0])
        if node.op in ("round", "floor", "ceil"):
            return f"{inner} {names[node.op]}"
        return f"{names.get(node.op, node.op + ' of')} {inner}"
    left, right = node.args
    if left.kind == "pct" and node.op == "*":
        return f"{spoken(left)} of {spoken(right)}"
    return f"{spoken(left)} {OP_WORDS[node.op]} {spoken(right)}"


def _run(node: Node, prec: int) -> tuple[Decimal, bool, bool]:
    """(value, inexact, approximate from a float step) at this precision."""
    approx = [False]
    with localcontext(_context(prec)) as ctx:
        try:
            value = _eval(node, approx)
        except Overflow:
            raise CalcError("The result is too large") from None
        except (Underflow, Subnormal):
            raise CalcError("The result is too small") from None  # review N2
        except DivisionByZero:
            raise CalcError("Division by zero") from None
        except InvalidOperation:
            raise CalcError("That has no real result") from None
        return value, bool(ctx.flags[Inexact]), approx[0]


def _short_exact(value: Decimal) -> bool:
    """Few enough digits that an inexact-flagged step (9^0.5) really was exact."""
    return len(value.normalize(Context(prec=200)).as_tuple().digits) <= PRECISION


def calculate(expression: str) -> dict[str, Any]:
    """Review N1: worked out at 34 and again at 80 digits. If the shown results differ,
    rounding inside lost digits (1e40 + 1 - 1e40): refused. Approximate when any step
    was inexact (unless the result is short enough to be exact) or the text is cut."""
    node = parse(str(expression))
    value, _, approx = _run(node, PRECISION)
    check, check_inexact, _ = _run(node, CHECK_PRECISION)
    result, rounded = show(value)
    if show(check)[0] != result:
        raise CalcError("That loses too many digits to answer exactly")
    # the 80-digit run decides inexactness (the 34-digit one is at least as lossy)
    approximate = rounded or approx or (check_inexact and not _short_exact(check))
    long_whole = not approximate and len(result.lstrip("-")) > SHOW_DIGITS
    about = "about " if approximate or long_whole else ""
    said = short(result) if about else result
    return {
        "result": result,
        "approximate": approximate,
        "said_as": f"{spoken(node)} is {about}{say_number(said)}",
    }
