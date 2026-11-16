"""SF1 (review of fix/spoken-followups): no part of a web address is ever read aloud.

A link address longer than the 200-character hold was split by the forced cut, and its
second half was spoken; a bare URL was never cleaned at all."""

from __future__ import annotations

import time

import pytest

from sasha.server.spoken import SpokenStream, clean_line, to_spoken

ADDRESS = "https://example.com/" + "a" * 260 + "/secret-path"


def streamed(text: str, size: int) -> str:
    stream = SpokenStream()
    out = "".join(stream.feed(text[i : i + size]) for i in range(0, len(text), size))
    return out + stream.finish()


@pytest.mark.parametrize("size", [1, 3, 7, 50, 1000])
def test_a_long_link_address_is_never_spoken(size: int) -> None:
    said = streamed(f"See [the docs]({ADDRESS}) for more. Then go on.", size)
    assert said == "See the docs for more. Then go on."
    assert "secret" not in said and "aaaa" not in said


@pytest.mark.parametrize("size", [1, 7, 1000])
def test_a_bare_url_is_said_as_its_host(size: int) -> None:
    said = streamed(f"see {ADDRESS} today. ok", size)
    assert said == "see example.com today. ok"


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("Visit www.example.org/path?q=1 now.", "Visit example.org now."),
        ("(see https://docs.example.net/a/b).", "(see docs.example.net)."),
        ("The [guide](https://x.example/g) helps.", "The guide helps."),
        ("Use e.g. a cloth, not 3.5 litres.", "Use e.g. a cloth, not 3.5 litres."),
    ],
)
def test_addresses_in_whole_text(text: str, said: str) -> None:
    assert to_spoken(text) == said


def test_an_answer_that_ends_inside_an_address_drops_it() -> None:
    said = streamed("See [the docs](" + "https://example.com/" + "b" * 400, 9)
    assert said == "See the docs"


def test_a_600_character_address_goes_whole() -> None:
    link = "[the docs](https://example.com/" + "c" * 600 + ")"
    assert clean_line(f"Read {link} first") == "Read the docs first"


@pytest.mark.parametrize(
    "hostile", ["](" * 100_000, "http://" * 50_000, "[a](http://" * 20_000, "www." * 50_000]
)
def test_address_patterns_stay_linear(hostile: str) -> None:
    started = time.perf_counter()
    clean_line(hostile)
    streamed(hostile[:20_000], 7)
    assert time.perf_counter() - started < 1.5
