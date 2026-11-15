"""Wake gate (server/wake.py): the name-only rules. The phrase comes from assistant.yaml."""

from __future__ import annotations

import pytest

from sasha.config import Settings, WakeConfig
from sasha.server import wake
from sasha.server.wake import WakeGate, WakeMatcher, is_yes


@pytest.fixture
def name(settings: Settings) -> str:
    return settings.assistant.wake_word.phrase  # the name alone, never hard-coded here


@pytest.fixture
def matcher(settings: Settings) -> WakeMatcher:
    ww, cfg = settings.assistant.wake_word, WakeConfig()
    return WakeMatcher(ww.phrase, ww.variants, cfg.fillers, cfg.continuations)


def test_phrase_is_the_name_alone(settings: Settings, name: str) -> None:
    assert name == settings.assistant.name
    assert isinstance(settings.assistant.wake_word.variants, list)  # filled from measurements


@pytest.mark.parametrize(
    ("template", "outcome", "command"),
    [
        ("{n}, turn off the lights", wake.ACCEPTED_WITH_COMMAND, "turn off the lights"),
        ("{n} what time is it?", wake.ACCEPTED_WITH_COMMAND, "what time is it?"),
        ("{N}. Lights on!", wake.ACCEPTED_WITH_COMMAND, "Lights on!"),
        ("Uh, hey {n}, lights on", wake.ACCEPTED_WITH_COMMAND, "lights on"),
        ("{n}.", wake.ACCEPTED_ALONE, None),
        ("  {n}?  ", wake.ACCEPTED_ALONE, None),
        ("thanks {n}", wake.REJECTED_MID_SENTENCE, None),
        ("I told {n} about it yesterday", wake.REJECTED_MID_SENTENCE, None),
        ("{n}'s phone is ringing", wake.REJECTED_POSSESSIVE, None),
        ("{n}" + chr(0x2019) + "s phone is ringing", wake.REJECTED_POSSESSIVE, None),
        ("{n} is coming over later", wake.REJECTED_CONTINUATION, None),
        ("{n} said no", wake.REJECTED_CONTINUATION, None),
        ("Na{l} is here", wake.REJECTED_NO_NAME, None),  # near name, part of a word
        ("{n}ka is here", wake.REJECTED_NO_NAME, None),
        ("the weather is nice", wake.REJECTED_NO_NAME, None),
        ("", wake.REJECTED_NO_NAME, None),
    ],
)
def test_matcher_rules(
    matcher: WakeMatcher, name: str, template: str, outcome: str, command: str | None
) -> None:
    text = template.format(n=name, N=name.upper(), l=name.lower())
    got_outcome, got_command, _ = matcher.match(text)
    assert (got_outcome, got_command) == (outcome, command)


def test_variant_spellings_match(name: str) -> None:
    variants = [f"Z{name[1:]}", f"{name}a"]  # spellings derived from the configured name
    matcher = WakeMatcher(name, variants, WakeConfig().fillers)
    for index, variant in enumerate(variants, start=1):
        outcome, command, matched = matcher.match(f"{variant}, lights on")
        assert (outcome, command, matched) == (wake.ACCEPTED_WITH_COMMAND, "lights on", index)
    assert matcher.match(f"{name}, lights on")[2] == 0


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_name_alone_arms_then_next_segment_is_the_command(matcher: WakeMatcher, name: str) -> None:
    clock = Clock()
    gate = WakeGate(matcher, armed_window_s=5, min_confidence=0.5, clock=clock)
    first = gate.decide(name, 0.9)
    assert first.outcome == wake.ACCEPTED_ALONE and first.heard and first.armed
    clock.now += 3
    second = gate.decide("turn on the kitchen light", 0.9)
    assert (second.outcome, second.command) == (wake.FOLLOWUP_USED, "turn on the kitchen light")
    assert not gate.armed


def test_expired_window_judges_the_next_segment_fresh(matcher: WakeMatcher, name: str) -> None:
    clock = Clock()
    gate = WakeGate(matcher, armed_window_s=5, min_confidence=0.5, clock=clock)
    gate.decide(name, 0.9)
    clock.now += 6
    late = gate.decide("turn on the kitchen light", 0.9)  # no name: a plain miss now
    assert late.outcome == wake.REJECTED_NO_NAME and not late.heard
    assert gate.expired() is False  # already disarmed


def test_expired_reports_once(matcher: WakeMatcher, name: str) -> None:
    clock = Clock()
    gate = WakeGate(matcher, armed_window_s=5, min_confidence=0.5, clock=clock)
    gate.decide(name, 0.9)
    assert gate.expired() is False
    clock.now += 5.5
    assert gate.expired() is True and gate.expired() is False


def test_low_confidence_name_alone_is_refused(matcher: WakeMatcher, name: str) -> None:
    gate = WakeGate(matcher, armed_window_s=5, min_confidence=0.5, clock=Clock())
    assert gate.decide(name, 0.2).outcome == wake.REJECTED_LOW_CONF
    assert not gate.armed
    # with a command, confidence isn't needed: the command itself is the evidence
    assert gate.decide(f"{name}, lights on", 0.2).outcome == wake.ACCEPTED_WITH_COMMAND


def test_name_again_while_armed(matcher: WakeMatcher, name: str) -> None:
    gate = WakeGate(matcher, armed_window_s=5, min_confidence=0.5, clock=Clock())
    gate.decide(name, 0.9)
    again = gate.decide(f"{name}, lights off", 0.9)
    assert (again.outcome, again.command) == (wake.FOLLOWUP_USED, "lights off")
    gate.decide(name, 0.9)
    assert gate.decide(name, 0.9).armed  # the name alone again keeps waiting


@pytest.mark.parametrize(
    ("text", "yes"),
    [
        ("Yes.", True),
        ("yeah do it", True),
        ("OK", True),
        ("no", False),
        ("", False),
        ("maybe yes", False),
        ("turn off the lights", False),
    ],
)
def test_is_yes(text: str, yes: bool) -> None:
    assert is_yes(text, WakeConfig().yes_words) is yes
