"""Typed and window turns: the server, not the model, says when no timer was set.

The worst timer failure is a reply like "I've set an alarm for 7:00" when no tool call
made one: the person relies on an alarm that doesn't exist. Wake turns already have
their truthful reply (server/wake_truth.py). For every other turn of a bot with timer
tools, the server adds its own line ("Nothing was set."):
- structurally, whatever the wording (T1): the request asked to set, change or cancel a
  timer, alarm or reminder, no changing timer tool succeeded this turn, and the reply
  isn't a question back. A plain fact, so no claim needs recognising;
- for any other turn, when the reply claims such a change: a claim word and a timer
  word in the same clause, with no negation right before the claim word ("Should I set
  an alarm?" and "I can't set alarms" are not claims; "No problem, alarm set" is).
On a typed chat (speak=0) the text streams as usual and the line comes after it. On a
spoken window turn (speak=1) whose request asks for a timer, all model text is held
until the turn ends, so a false sentence is never spoken; the line replaces it. The
words come from `profile.timers` (per language later).
"""

from __future__ import annotations

import re
from typing import Any

from sasha.config_timers import TimersConfig

CHANGING_TOOLS = ("set_timer", "set_alarm", "set_reminder", "cancel_timer", "snooze_timer")
NEGATION_REACH = 2  # a negation counts only this close before the claim word
_CLAUSE = re.compile(r"[^.!?,;:]+")
_NOT_NEGATIONS = (("no", "problem"), ("no", "worries"), ("no", "doubt"))


def _words(text: str) -> list[str]:
    text = text.lower().replace(chr(0x2019), "'")
    return [re.sub(r"'s$", "", w) for w in re.findall(r"[\w']+", text)]  # "alarm's": alarm


def _negated(words: list[str], at: int, negation: set[str]) -> bool:
    for i in range(max(0, at - NEGATION_REACH), at):
        if words[i] in negation and tuple(words[i : i + 2]) not in _NOT_NEGATIONS:
            return True
    return False


def claims_timer(text: str, cfg: TimersConfig) -> bool:
    claim, timer, negation = set(cfg.claim_words), set(cfg.timer_words), set(cfg.negation_words)
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if sentence.strip().endswith("?"):
            continue
        # "I set it for 7, your alarm will ring then." claims across its clauses: judge
        # the sentence's claim words by clause, its timer words anywhere in it.
        words = _words(sentence)
        if not set(words) & timer:
            continue
        for clause in _CLAUSE.findall(sentence):
            clause_words = _words(clause)
            for i, w in enumerate(clause_words):
                if w in claim and not _negated(clause_words, i, negation):
                    return True
    return False


_TIME_OBJECTS = {"me", "it", "this", "that", "my", "the", "a", "an"}  # "time the eggs"
_NUMBER_WORDS = ("one", "two", "three", "four", "five", "ten", "fifteen", "twenty", "thirty")


def asks_for_timer(command: str, cfg: TimersConfig) -> bool:
    """A timer request: any timer word or timer verb ("wake me at 7", "ping me in 20",
    "can you time 10 minutes?", "turn off the alarm"), unless it is a status question
    ("how long is left on my timer?"): it ends in "?" and opens with a wh-word or is,
    are, do... "time" counts only as a verb, so "what time is it" is no request."""
    words = _words(command)
    if command.strip().endswith("?") and words and words[0] in set(cfg.status_starts):
        return False
    if set(words) & (set(cfg.timer_words) | set(cfg.timer_verbs)):
        return True
    return any(
        w == "time" and i + 1 < len(words) and (words[i + 1].isdigit() or words[i + 1] in
                                                 _TIME_OBJECTS | set(_NUMBER_WORDS))
        for i, w in enumerate(words)
    )  # fmt: skip


def asks_back(text: str) -> bool:
    """The reply is a question back ("Morning or evening?"): the server leaves it alone."""
    return text.strip().endswith("?")


class TimerTruth:
    """One per typed or window turn of a bot with timer tools."""

    def __init__(self, cfg: TimersConfig, command: str, spoken: bool) -> None:
        self.cfg = cfg
        self.requested = asks_for_timer(command, cfg)
        self.holding = spoken and self.requested  # never speak a false claim
        self.held = ""

    def feed(self, piece: str) -> str:
        """Streamed model text in (worker thread); the part to send and speak now out."""
        if self.holding:
            self.held += piece
            return ""
        return piece

    def finish(self, text: str, tool_calls: list[dict[str, Any]]) -> tuple[str, str]:
        """(text still to send and speak, the final reply text)."""
        made = any(c.get("name") in CHANGING_TOOLS and c.get("ok") for c in tool_calls)
        asked_back = asks_back(text)
        # "Your alarm is set for 7." read from list_timers is a report, not a claim.
        listed = any(c.get("name") == "list_timers" and c.get("ok") for c in tool_calls)
        if self.requested:
            needs_line = not made and not asked_back  # T1: structural, any wording
        else:
            needs_line = not made and not listed and claims_timer(text, self.cfg)
        if not needs_line:
            return self.held, text
        line = self.cfg.nothing_set
        if self.holding:  # nothing was spoken yet: the server's line instead
            return line, line
        return f" {line}", f"{text.rstrip()} {line}"
