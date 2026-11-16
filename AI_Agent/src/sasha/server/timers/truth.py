"""Typed and window turns: the server, not the model, says what is really set.

The worst timer failure is a reply like "I've set an alarm for 7:00" when no tool call
made one: the person relies on an alarm that doesn't exist. Wake turns already have
their truthful reply (server/wake_truth.py). For every other turn of a bot with timer
tools, a false claim is never spoken:
- a timer tool asked the person back this turn (an ambiguous period, a missing time...):
  the server asks its own fixed question for that reason (`ask_lines`);
- a request (Q2: a timer verb, or a timer word with a request verb or a time
  expression), or an answer to the previous reply's question about one ("Morning."),
  that set nothing gets the line ("Nothing was set.") whatever the wording (T1, Q1).
  Only a reply that is all questions ("Morning or evening?") is left alone: "Done.
  Anything else?" is a claim with a question after it;
- a status question ("is my alarm set?") answered without `list_timers` gets the real
  state from the timers service instead (status.py);
- any other turn gets the line when the reply claims a change: a claim word and a timer
  word in the same clause, no negation right before ("No problem, alarm set" claims).
On a typed chat (speak=0) the text streams as usual and the server's text comes after
it. On a spoken window turn (speak=1) for a request, an answer or a status question, the
model's text is held until the turn ends and replaced when the server speaks: by its
question, by the line and a question (the reply's own last question if it asks for a
time, else a fixed one; the line alone after a cancel request), or by the real state.
The history keeps the reply as shown. Words come from `profile.timers`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sasha.config_timers import TimersConfig

CHANGING_TOOLS = ("set_timer", "set_alarm", "set_reminder", "cancel_timer", "snooze_timer")
NEGATION_REACH = 2  # a negation counts only this close before the claim word
_CLAUSE = re.compile(r"[^.!?,;:]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NOT_NEGATIONS = (("no", "problem"), ("no", "worries"), ("no", "doubt"))
_TIME_OBJECTS = {"me", "it", "this", "that", "my", "the", "a", "an"}  # "time the eggs"


def _words(text: str) -> list[str]:
    text = text.lower().replace(chr(0x2019), "'")
    return [re.sub(r"'s$", "", w) for w in re.findall(r"[\w']+", text)]  # "alarm's": alarm


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def _negated(words: list[str], at: int, negation: set[str]) -> bool:
    for i in range(max(0, at - NEGATION_REACH), at):
        if words[i] in negation and tuple(words[i : i + 2]) not in _NOT_NEGATIONS:
            return True
    return False


def _offered(words: list[str], at: int, cfg: TimersConfig) -> bool:
    """An offer right before the claim word: "happy to set", "I can set"."""
    return any(words[:at][-len(o.split()) :] == o.split() for o in cfg.offer_words)


def _claim_at(words: list[str], cfg: TimersConfig, offers: bool) -> bool:
    claim, negation = set(cfg.claim_words), set(cfg.negation_words)
    return any(
        w in claim and not _negated(words, i, negation) and not (offers and _offered(words, i, cfg))
        for i, w in enumerate(words)
    )


def claims_timer(text: str, cfg: TimersConfig) -> bool:
    """A claim word and a timer word in a sentence that isn't a question, the claim
    word not negated: "I set it for 7, your alarm will ring then." claims."""
    timer = set(cfg.timer_words)
    for sentence in _sentences(text):
        if sentence.endswith("?") or not set(_words(sentence)) & timer:
            continue
        if any(_claim_at(_words(c), cfg, offers=False) for c in _CLAUSE.findall(sentence)):
            return True
    return False


def claims_anything(text: str, cfg: TimersConfig) -> bool:
    """Any claim word outside questions, timer word or not ("Done.", "All set!"), and not
    an offer ("happy to set"): picks "Nothing was set." over "Nothing is set yet."."""
    return any(
        _claim_at(_words(c), cfg, offers=True)
        for s in _sentences(text)
        if not s.endswith("?")
        for c in _CLAUSE.findall(s)
    )


def all_questions(text: str) -> bool:
    """Every sentence is a question ("Morning or evening?", "What time? Today?")."""
    sentences = _sentences(text)
    return bool(sentences) and all(s.endswith("?") for s in sentences)


def is_status_question(command: str, cfg: TimersConfig) -> bool:
    """Ends in "?" and opens with a wh-word or is, are, do... ("is my alarm set?")."""
    words = _words(command)
    return command.strip().endswith("?") and bool(words) and words[0] in set(cfg.status_starts)


def asks_for_timer(command: str, cfg: TimersConfig) -> bool:
    """A timer request (Q2): a timer verb ("wake me at 7", "time 10 minutes"), or a timer
    word with a request verb or phrase ("set", "turn off") or a time expression ("ten
    minute timer", "alarm at 7"); never a status question."""
    if is_status_question(command, cfg):
        return False
    words = _words(command)
    said, joined = set(words), " ".join(words)
    if said & set(cfg.timer_verbs):
        return True
    numbers = set(next(iter(cfg.cues.values())).number_values) if cfg.cues else set()
    if any(
        w == "time" and i + 1 < len(words) and (any(ch.isdigit() for ch in words[i + 1])
                                                 or words[i + 1] in _TIME_OBJECTS | numbers)
        for i, w in enumerate(words)
    ):  # fmt: skip
        return True
    if not said & set(cfg.timer_words):
        return False
    timed = any(any(ch.isdigit() for ch in w) for w in words) or bool(
        said & (set(cfg.time_expression_words) | numbers)
    )
    phrased = any(f" {p} " in f" {joined} " for p in cfg.request_phrases)
    return bool(said & set(cfg.request_verbs)) or phrased or timed


def pending_ask(tool_calls: list[dict[str, Any]]) -> tuple[str, str] | None:
    """(tool, reason) of the last ask of a changing timer tool this turn, unless a later
    call of the same tool succeeded (the model asked, got the answer, and set it)."""
    asks: list[tuple[str, str]] = []
    for call in tool_calls:
        name = str(call.get("name"))
        if name not in CHANGING_TOOLS:
            continue
        if call.get("ok"):
            asks = [a for a in asks if a[0] != name]
        elif call.get("asks"):
            asks.append((name, str(call["asks"])))
    return asks[-1] if asks else None


class TimerTruth:
    """One per typed or window turn of a bot with timer tools. `answering`: the command
    answers the previous reply's question about a timer request, so it counts as that
    request. `state`: the real state for the kinds a status question names (status.py)."""

    def __init__(
        self,
        cfg: TimersConfig,
        command: str,
        spoken: bool,
        answering: bool = False,
        state: Callable[[list[str]], str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.words = _words(command)
        self.requested = answering or asks_for_timer(command, cfg)
        said = set(self.words)
        # A status question about the owner's own items ("is my alarm set?"), not about
        # timers in general ("what's a good timer for tea?").
        status = (
            is_status_question(command, cfg)
            and bool(said & set(cfg.timer_words))
            and bool(said & set(cfg.status_words))
        )
        self.state = state if status and not self.requested else None
        self.cancelling = bool(said & set(cfg.cancel_words))
        # "For how long?" for a timer, "What time should it be?" for anything else.
        timer = said & set(cfg.timer_only_words)
        self.generic = cfg.ask_generic_timer if timer else cfg.ask_generic
        self.holding = spoken and (self.requested or self.state is not None)
        self.held = ""

    def feed(self, piece: str) -> str:
        """Streamed model text in (worker thread); the part to send and speak now out."""
        if self.holding:
            self.held += piece
            return ""
        return piece

    def question(self, tool: str, reason: str) -> str:
        """The server's own question for a tool's ask ("" for none)."""
        lines = self.cfg.ask_lines
        return lines.get(f"{tool}.{reason}", lines.get(reason, self.cfg.ask_generic))

    def _line(self, text: str) -> str:
        if self.cancelling:  # "turn off the alarm": nothing was changed
            return self.cfg.nothing_changed
        return self.cfg.nothing_set if claims_anything(text, self.cfg) else self.cfg.nothing_yet

    def _replace(self, text: str, line: str) -> str:
        """The spoken replacement: the line and the reply's last question if it asks for
        a time ("What time?"), else the fixed one; the line alone after a cancel request."""
        if self.cancelling:
            return line
        asking = set(self.cfg.asking_words)
        kept = [
            q
            for q in _sentences(text)
            if q.endswith("?") and set(_words(q)) & asking and not claims_anything(q, self.cfg)
        ]
        return f"{line} {kept[-1] if kept else self.generic}"

    def finish(self, text: str, tool_calls: list[dict[str, Any]]) -> tuple[str, str]:
        """(text still to send and speak, the final reply text)."""
        made = any(c.get("name") in CHANGING_TOOLS and c.get("ok") for c in tool_calls)
        ask = pending_ask(tool_calls)
        if ask is not None and (question := self.question(*ask)):
            # A tool asked back: the question comes from the server, never a false claim.
            if self.holding and not made:
                return question, question
            if all_questions(text):
                return self.held, text
            if self.holding:  # something was made: what was held, then the question
                return f"{self.held.rstrip()} {question}", f"{text.rstrip()} {question}"
            line = f"{self.cfg.nothing_set} " if claims_anything(text, self.cfg) else ""
            return f" {line}{question}", f"{text.rstrip()} {line}{question}"
        listed = any(c.get("name") == "list_timers" and c.get("ok") for c in tool_calls)
        if self.state is not None:  # a status question: the real state unless it listed
            if listed:
                return self.held, text
            real = self.state(self.words)
            return (real, real) if self.holding else (f" {real}", f"{text.rstrip()} {real}")
        if self.requested:
            needs_line = not made and not all_questions(text)  # T1 and Q1: structural
        else:  # "Your alarm is set for 7." read from list_timers is a report, not a claim
            needs_line = not made and not listed and claims_timer(text, self.cfg)
        if not needs_line:
            return self.held, text
        line = self._line(text) if self.requested else self.cfg.nothing_set
        if self.holding:  # nothing was spoken yet: the server's line (and question) instead
            spoken = self._replace(text, line)
            return spoken, spoken
        return f" {line}", f"{text.rstrip()} {line}"
