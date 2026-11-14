"""Speaker: the first piece of an answer starts speaking early."""

from __future__ import annotations

import asyncio
import threading

import pytest

from sasha.pipeline.tts import split_sentences
from sasha.server.speech import Speaker, cut_first
from tests.server_fakes import FakeTTS


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Sure, I turned on the bedroom light, and the fan",
            ("Sure, I turned on the bedroom light,", "and the fan"),
        ),
        ("Sure, the light", None),  # only a 5-char head before the pause
        ("No pause marks here at all in this long text", None),
        ("All the lights are on; the fan is off", ("All the lights are on;", "the fan is off")),
        ("Paris is the capital. It is big, old", ("Paris is the capital.", "It is big, old")),
    ],
)
def test_cut_first(text: str, expected: tuple[str, str] | None) -> None:
    assert cut_first(text) == expected


def _speak(deltas: list[str]) -> list[str]:
    async def main() -> list[str]:
        tts = FakeTTS()
        sp = Speaker(tts, "en", split_sentences, asyncio.Lock(), lambda f: None, threading.Event())
        for d in deltas:
            sp.feed(d)
            await asyncio.sleep(0)
        await sp.finish()
        return [text for text, _ in tts.spoken]

    return asyncio.run(main())


def test_first_piece_is_cut_at_a_pause_once_long_enough() -> None:
    words = "Cats can rotate their ears 180 degrees, which helps them hear prey. They nap a lot."
    spoken = _speak([w + " " for w in words.split()])
    assert spoken[0] == "Cats can rotate their ears 180 degrees,"
    assert spoken[1:] == ["which helps them hear prey.", "They nap a lot."]


def test_short_first_sentence_is_queued_when_it_ends() -> None:
    async def main() -> list[str]:
        tts = FakeTTS()
        sp = Speaker(tts, "en", split_sentences, asyncio.Lock(), lambda f: None, threading.Event())
        for d in ["The capital ", "of France is ", "Paris.", " It"]:
            sp.feed(d)
        queued = [sp.queue.get_nowait()]
        await sp.finish()
        return queued

    # Queued at " It": the splitter alone would hold it until "It ..." reached 12 chars.
    assert asyncio.run(main()) == ["The capital of France is Paris."]


def test_only_the_first_piece_is_cut_early() -> None:
    text = "The capital of France is Paris. It is big, old, famous, lovely, and full of cafes."
    spoken = _speak([w + " " for w in text.split()])
    assert spoken == [
        "The capital of France is Paris.",
        "It is big, old, famous, lovely, and full of cafes.",
    ]
