"""False wakes (2026-11-16): "phrase, command" needs evidence the phrase was said, not
only the phrase written by the hinted decoder at the start of a long segment of other
people's speech. The phrase comes from assistant.yaml; the numbers are made up."""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any

import pytest

from sasha.config import Settings, WakeConfig
from sasha.pipeline.stt import Transcript, Word
from sasha.server import wake
from sasha.server.wake import WakeGate, WakeMatcher
from sasha.server.wake_evidence import hear, phrase_evidence


@pytest.fixture
def matcher(settings: Settings) -> WakeMatcher:
    ww, cfg = settings.assistant.wake_word, WakeConfig()
    return WakeMatcher(ww.phrase, ww.variants, cfg.fillers, cfg.continuations, ww.word_variants)


def words(text: str, probs: list[float]) -> tuple[Word, ...]:
    """The decoder's words for `text`, with these probabilities (one per word)."""
    parts = text.split()
    return tuple(
        Word(w, p, i * 0.3, i * 0.3 + 0.3)
        for i, (w, p) in enumerate(zip(parts, probs, strict=True))
    )


def gate(matcher: WakeMatcher) -> WakeGate:
    cfg = WakeConfig()
    return WakeGate(
        matcher,
        cfg.armed_window_s,
        cfg.min_confidence,
        min_command_confidence=cfg.min_command_confidence,
        sure_command_confidence=cfg.sure_command_confidence,
        recheck_from_s=cfg.recheck_from_s,
    )


def first_word(matcher: WakeMatcher) -> str:
    return matcher.phrase.split()[0]


def test_a_low_average_on_a_command_is_refused(matcher: WakeMatcher) -> None:
    """The telemetry's false wakes averaged 0.26-0.35 (and 0.46); real commands 0.38 up."""
    text = f"{matcher.phrase} what time is it"
    g = gate(matcher)
    assert not g.needs_recheck(text, 0.31, 6.0)  # refused outright: no second decode
    assert g.decide(text, 0.31, seconds=6.0).outcome == wake.REJECTED_LOW_CONF
    assert g.decide(text, 0.62, seconds=6.0).heard  # sure: no recheck, as before


def test_a_doubtful_long_wake_is_decoded_again_without_the_hint(matcher: WakeMatcher) -> None:
    text = f"{matcher.phrase} turn on the kitchen light"
    g = gate(matcher)
    assert g.needs_recheck(text, 0.42, 6.0)
    other = "and the home side leading by two goals"  # what was really said
    d = g.decide(text, 0.42, seconds=6.0, plain=other)
    assert d.outcome == wake.REJECTED_UNCONFIRMED and d.command is None
    assert g.decide(text, 0.42, seconds=6.0).outcome == wake.REJECTED_UNCONFIRMED  # no plain
    # The owner's accent: without the hint the name comes out as anything, "hey" doesn't.
    said = f"{first_word(matcher).capitalize()} Shasha, turn on the kitchen light."
    d = g.decide(text, 0.42, seconds=6.0, plain=said)
    assert d.outcome == wake.ACCEPTED_WITH_COMMAND and d.rechecked
    assert d.command == "turn on the kitchen light"  # the hinted decode's command


def test_a_short_doubtful_wake_is_not_rechecked(matcher: WakeMatcher) -> None:
    """Real "phrase, command" segments are 2.6-3.8 s (bench); the false ones 5.4-7.5 s."""
    text = f"{matcher.phrase} what time is it"
    g = gate(matcher)
    assert not g.needs_recheck(text, 0.42, 3.0)
    assert g.decide(text, 0.42, seconds=3.0).outcome == wake.ACCEPTED_WITH_COMMAND


def test_the_name_heard_without_help_counts(matcher: WakeMatcher) -> None:
    """Review F1: without the hint "hey" often comes out as "hi"; the name decoded
    without the hint is the strongest evidence there is."""
    name = matcher.phrase.split()[-1]
    g = gate(matcher)
    text = f"{matcher.phrase} turn on the light"
    for plain in (f"Hi {name}, turn on the light.", f"A {name} turn on the light"):
        assert matcher.opens_like_phrase(plain), plain
        assert g.decide(text, 0.42, seconds=4.5, plain=plain).outcome == (
            wake.ACCEPTED_WITH_COMMAND
        )
    assert not matcher.opens_like_phrase(f"Turn on the light, {name}")  # third word: no


def test_a_first_word_spelling_counts() -> None:
    """word_variants count in the recheck as in the match (review F1: "Hay, …")."""
    m = WakeMatcher("hey Testname", fillers=["um", "hey"], word_variants={"hey": ["hay"]})
    assert m.opens_like_phrase("Hay, turn on the light.")
    assert m.opens_like_phrase("um hay what time is it")
    assert not m.opens_like_phrase("Hello, turn on the light.")
    assert WakeMatcher("Testname").opens_like_phrase("so Testname, lights")  # one word
    assert not WakeMatcher("Testname").opens_like_phrase("Hey, lights on")


def test_opens_like_phrase(matcher: WakeMatcher) -> None:
    hey = first_word(matcher)
    assert matcher.opens_like_phrase(f"{matcher.phrase}, lights on")
    assert matcher.opens_like_phrase(f"um, {hey.upper()}! Shasha what time is it")
    assert not matcher.opens_like_phrase("Heads up, what time is it")
    assert not matcher.opens_like_phrase(f"so I said {hey} to him")
    assert not matcher.opens_like_phrase("")


def test_the_phrase_alone_and_the_follow_up_are_unchanged(matcher: WakeMatcher) -> None:
    g = gate(matcher)
    text = matcher.phrase
    weak = words(text, [0.02] + [0.1] * (len(text.split()) - 1))
    d = g.decide(text, 0.55, weak, seconds=6.0)
    assert d.outcome == wake.ACCEPTED_ALONE and d.phrase_p == 0.1  # recorded, not judged
    assert not g.needs_recheck("turn on the light", 0.2, 6.0)  # armed: never rechecked
    assert g.decide("turn on the light", 0.2).outcome == wake.FOLLOWUP_USED


def test_the_phrase_number_leaves_out_the_segments_first_word(matcher: WakeMatcher) -> None:
    """Under the hint the first word always scores about 0.02 (bench, 2026-11-16)."""
    text = f"{matcher.phrase} lights on"
    n = len(matcher.phrase.split())
    ev = phrase_evidence(text, words(text, [0.02] + [0.85] * (n - 1) + [0.9, 0.9]),
                         matcher.phrase_tokens(text))  # fmt: skip
    assert ev is not None and ev.probability == (0.85 if n > 1 else 0.02)
    filler = f"um, {matcher.phrase}, lights on"
    ws = words(re.sub(",", "", filler), [0.01, 0.3] + [0.8] * (n - 1) + [0.9, 0.9])
    ev = phrase_evidence(filler, ws, matcher.phrase_tokens(filler))
    assert ev is not None and ev.probability == 0.3  # after a filler the phrase's first counts
    assert phrase_evidence(text, words("something else", [0.9, 0.9]), (0, n)) is None


class FakeSTT:
    def __init__(self, hinted: str, plain: str, confidence: float) -> None:
        self.hinted, self.plain, self.confidence = hinted, plain, confidence
        self.calls: list[str | None] = []

    def transcribe(
        self, audio: bytes, rate: int, *, hotwords: str | None = None, opening: bool = False
    ) -> Transcript:
        self.calls.append("opening" if opening else hotwords)
        text = self.hinted if hotwords else self.plain
        return Transcript(text=text, language="en", confidence=self.confidence)


def test_hear_decodes_again_only_when_doubtful(matcher: WakeMatcher) -> None:
    long_audio = bytes(2 * 16000 * 6)  # 6 s of 16-bit silence
    text = f"{matcher.phrase} turn on the light"

    def run(confidence: float) -> tuple[Any, list[str | None]]:
        stt = FakeSTT(text, "the home side leading", confidence)
        lock: Any = contextlib.AsyncExitStack()  # any async context manager
        heard, plain, _ = asyncio.run(
            hear(stt, lock, long_audio, 16000, matcher.phrase, gate(matcher))
        )
        return (heard.text, plain), stt.calls

    assert run(0.7) == ((text, None), [matcher.phrase])
    stt = FakeSTT(text, "unused", 0.42)  # a stop-word segment: judged at once
    asyncio.run(hear(stt, contextlib.AsyncExitStack(), long_audio, 16000, matcher.phrase,
                     gate(matcher), recheck=False))  # fmt: skip
    assert stt.calls == [matcher.phrase]
    # Review N1: decoded without the hint (the armed window ran out meanwhile): the
    # transcript is the plain decode itself, so a real wake is not refused for want of one.
    stt = FakeSTT("unused", text, 0.42)
    heard, plain, _ = asyncio.run(
        hear(stt, contextlib.AsyncExitStack(), long_audio, 16000, None, gate(matcher))
    )
    assert plain == heard.text == text and stt.calls == [None]
    assert gate(matcher).decide(heard.text, 0.42, seconds=6.0, plain=plain).heard
    assert run(0.42) == ((text, "the home side leading"), [matcher.phrase, "opening"])


def test_the_decoder_options() -> None:
    """Review S2: the recheck is one pass at temperature 0 for a few tokens, no words."""
    from sasha.pipeline.stt_whisper import OPENING_TOKENS, decode_options

    assert decode_options("hey x", opening=True) == {
        "temperature": 0.0,
        "max_new_tokens": OPENING_TOKENS,
    }
    assert decode_options("hey x", opening=False) == {"hotwords": "hey x", "word_timestamps": True}
    assert decode_options(None, opening=False) == {}
