"""Wake-word gate, v1: decide from a transcript whether the assistant was addressed.

Pure logic: no audio, no model, no Assistant, no telemetry. The caller (wake_session)
transcribes a segment, asks `WakeGate.decide`, and on anything but a hit throws the
text away. The phrase and its spellings come from assistant.yaml (`wake_word`), never
from code.

Rules (Dependencies/team/wake-word-design.md, section 2):
- the name must be the FIRST word, after optional fillers ("uh", "hey", "ok");
- "name, command" is a hit with the rest as the command;
- the name alone arms a short window: the next segment is the command;
- refused: the name mid-sentence or at the end, a possessive ("name's"), a word that
  means the name is talked about ("name is ..."), near names (whole-word match only),
  and the name alone below a confidence threshold.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Outcomes. Only these labels (never text) go to telemetry.
ACCEPTED_WITH_COMMAND = "accepted_with_command"
ACCEPTED_ALONE = "accepted_alone"
FOLLOWUP_USED = "followup_used"
REJECTED_NO_NAME = "rejected_no_name"
REJECTED_MID_SENTENCE = "rejected_mid_sentence"
REJECTED_POSSESSIVE = "rejected_possessive"
REJECTED_CONTINUATION = "rejected_continuation"
REJECTED_LOW_CONF = "rejected_low_conf"
HITS = frozenset({ACCEPTED_WITH_COMMAND, ACCEPTED_ALONE, FOLLOWUP_USED})

_APOSTROPHES = ("'", chr(0x2019))  # straight and typographic (right single quote)


@dataclass(frozen=True)
class WakeDecision:
    outcome: str
    command: str | None = None  # set only when a turn should run
    variant: int = -1  # which spelling matched (0 = the phrase itself); for telemetry

    @property
    def heard(self) -> bool:
        return self.outcome in HITS

    @property
    def armed(self) -> bool:
        return self.outcome == ACCEPTED_ALONE


def _words_pattern(text: str) -> str:
    words = re.findall(r"\w+", text.lower())
    return r"\W+".join(re.escape(w) for w in words)


class WakeMatcher:
    """Finds the wake phrase at the start of a transcript."""

    def __init__(
        self,
        phrase: str,
        variants: Sequence[str] = (),
        fillers: Sequence[str] = (),
        continuations: Sequence[str] = (),
    ) -> None:
        forms = [f for f in (_words_pattern(p) for p in (phrase, *variants)) if f]
        if not forms:
            raise ValueError("wake_word.phrase has no words")
        self.forms = forms
        filler = "|".join(re.escape(f.lower()) for f in fillers) or r"(?!)"
        self._lead = rf"^\W*(?:(?:{filler})\W+)*"
        self._continuations = {c.lower() for c in continuations}

    def match(self, text: str) -> tuple[str, str | None, int]:
        """(outcome, command or None, variant index). Never raises on odd input."""
        for index, form in enumerate(self.forms):
            m = re.match(rf"{self._lead}(?:{form})(?!\w)", text, re.IGNORECASE)
            if not m:
                continue
            rest = text[m.end() :]
            if rest[:1] in _APOSTROPHES and rest[1:2].lower() == "s":
                return REJECTED_POSSESSIVE, None, index
            command = rest.lstrip(" \t,.;:!?-").strip()
            first = re.match(r"\w+", command.lower())
            if first and first.group(0) in self._continuations:
                return REJECTED_CONTINUATION, None, index
            if not command:
                return ACCEPTED_ALONE, None, index
            return ACCEPTED_WITH_COMMAND, command, index
        for form in self.forms:
            if re.search(rf"(?<!\w)(?:{form})(?!\w)", text, re.IGNORECASE):
                return REJECTED_MID_SENTENCE, None, -1
        return REJECTED_NO_NAME, None, -1


class WakeGate:
    """Per-listener decision state: the armed window after the name alone."""

    def __init__(
        self,
        matcher: WakeMatcher,
        armed_window_s: float,
        min_confidence: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.matcher = matcher
        self.armed_window_s = armed_window_s
        self.min_confidence = min_confidence
        self.clock = clock
        self.armed_until: float | None = None

    @property
    def armed(self) -> bool:
        return self.armed_until is not None and self.clock() <= self.armed_until

    def disarm(self) -> None:
        self.armed_until = None

    def expired(self) -> bool:
        """True once (and disarms) if an armed window ran out without a segment."""
        if self.armed_until is not None and self.clock() > self.armed_until:
            self.armed_until = None
            return True
        return False

    def decide(self, text: str, confidence: float | None) -> WakeDecision:
        if self.armed:
            self.armed_until = None
            outcome, command, variant = self.matcher.match(text)
            if outcome == ACCEPTED_WITH_COMMAND:  # said the name again: use what follows
                return WakeDecision(FOLLOWUP_USED, command, variant)
            if outcome == ACCEPTED_ALONE:  # the name again, alone: keep waiting
                self.armed_until = self.clock() + self.armed_window_s
                return WakeDecision(ACCEPTED_ALONE, variant=variant)
            command = text.strip()
            if command and outcome != REJECTED_POSSESSIVE:
                return WakeDecision(FOLLOWUP_USED, command)
            return WakeDecision(REJECTED_NO_NAME)
        self.expired()  # a segment after the window is judged fresh, like any other
        outcome, command, variant = self.matcher.match(text)
        if outcome == ACCEPTED_ALONE:
            if confidence is not None and confidence < self.min_confidence:
                return WakeDecision(REJECTED_LOW_CONF, variant=variant)
            self.armed_until = self.clock() + self.armed_window_s
        return WakeDecision(outcome, command, variant)


def is_yes(text: str, yes_words: Sequence[str]) -> bool:
    """A confirmation answer: only a clear yes counts; anything else is no."""
    first = re.match(r"\W*(\w+)", text.lower())
    return first is not None and first.group(1) in {w.lower() for w in yes_words}


NEGATIONS = frozenset({"no", "not", "don't", "dont", "wait", "stop", "cancel", "never"})
MAX_YES_WORDS = 4


def confirm_reason(
    text: str,
    yes_words: Sequence[str],
    no_words: Sequence[str],
    fillers: Sequence[str] = (),
) -> str:
    """`yes`, `no` or `unclear` for a spoken answer. Only `yes` accepts.

    Yes only if, after leading fillers, the answer starts with a yes-word, has at
    most 4 words and contains no no-word or negation ("yes, no wait" is not a yes).
    "no" if it starts with a no-word; anything else is "unclear" (which also means no).
    """
    yes = {w.lower() for w in yes_words}
    no = {w.lower() for w in no_words}
    skip = {f.lower() for f in fillers} - yes  # "ok" is a yes, not a filler, here
    words = re.findall(r"[\w']+", text.lower().replace(chr(0x2019), "'"))
    while words and words[0] in skip:
        words.pop(0)
    if not words:
        return "unclear"
    if words[0] in no:
        return "no"
    negated = any(w in NEGATIONS or w in no for w in words)
    if words[0] in yes and len(words) <= MAX_YES_WORDS and not negated:
        return "yes"
    return "unclear"
