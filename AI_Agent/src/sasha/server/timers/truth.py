"""Typed and window turns: the server, not the model, says when no timer was set.

The worst timer failure is a reply like "I've set an alarm for 7:00" when no tool call
made one: the person relies on an alarm that doesn't exist. Wake turns already have
their truthful reply (server/wake_truth.py). For every other turn of a bot with timer
tools: if the reply claims a timer, alarm or reminder was set, changed or cancelled and
no timer tool succeeded this turn, the server adds its own line ("Nothing was set.").
- On a typed chat (speak=0) the text streams as usual; the line comes after it.
- On a spoken window turn (speak=1) whose request asks for a timer, all model text is
  held until the turn ends, so a false sentence is never spoken; the line replaces it.
A claim is a claim word near a timer word in a sentence that isn't a question and has
no negation ("Should I set an alarm?" and "I can't set alarms" are not claims). The
words come from `profile.timers` (per language later).
"""

from __future__ import annotations

import re
from typing import Any

from sasha.config_timers import TimersConfig

CHANGING_TOOLS = ("set_timer", "set_alarm", "set_reminder", "cancel_timer", "snooze_timer")
NEAR = 4  # a claim word within this many words of a timer word
_SENTENCE = re.compile(r"[^.!?]+[.!?]*")


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower().replace(chr(0x2019), "'"))


def claims_timer(text: str, cfg: TimersConfig) -> bool:
    claim, timer, negation = set(cfg.claim_words), set(cfg.timer_words), set(cfg.negation_words)
    for sentence in _SENTENCE.findall(text):
        if sentence.strip().endswith("?"):
            continue
        words = _words(sentence)
        if set(words) & negation:
            continue
        at_claim = [i for i, w in enumerate(words) if w in claim]
        at_timer = [i for i, w in enumerate(words) if w in timer]
        if any(abs(c - t) <= NEAR for c in at_claim for t in at_timer):
            return True
    return False


def asks_for_timer(command: str, cfg: TimersConfig) -> bool:
    return bool(set(_words(command)) & set(cfg.timer_words))


class TimerTruth:
    """One per typed or window turn of a bot with timer tools."""

    def __init__(self, cfg: TimersConfig, command: str, spoken: bool) -> None:
        self.cfg = cfg
        self.holding = spoken and asks_for_timer(command, cfg)  # never speak a false claim
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
        if made or not claims_timer(text, self.cfg):
            return self.held, text
        line = self.cfg.nothing_set
        if self.holding:  # nothing was spoken yet: the server's line instead
            return line, line
        return f" {line}", f"{text.rstrip()} {line}"
