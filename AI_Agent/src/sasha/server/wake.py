"""Wake-word gate, v1: decide from a transcript whether the assistant was addressed.

Pure logic: no audio, no model, no Assistant, no telemetry. The caller (wake_session)
transcribes a segment, asks `WakeGate.decide`, and on anything but a hit throws the
text away. The phrase and its spellings come from assistant.yaml (`wake_word`), never
from code.

Rules (Dependencies/team/wake-word-design.md, section 2; the phrase became "hey" plus
the name on 2026-11-16, the owner's decision):
- the phrase must come FIRST, after optional fillers ("uh", "ok");
- "phrase, command" is a hit with the rest as the command;
- the phrase alone arms a short window: the next segment is the command;
- refused: the name mid-sentence or at the end, a possessive ("name's"), a word that
  means the name is talked about ("name is ..."), near names (whole-word match only),
  and the name alone below a confidence threshold;
- "phrase, command" also needs evidence that the phrase was said (false wakes on
  2026-11-16: long stretches of other people's speech, decoded with the phrase as a
  hint, got it written at the start): below `min_command_confidence` it is refused;
  below `sure_command_confidence` on a segment of `recheck_from_s` or longer it is
  decoded again without the hint, and the phrase's first word must be there too.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from sasha.pipeline.stt import Word
from sasha.server.wake_evidence import phrase_evidence

# Outcomes. Only these labels (never text) go to telemetry.
ACCEPTED_WITH_COMMAND = "accepted_with_command"
ACCEPTED_ALONE = "accepted_alone"
FOLLOWUP_USED = "followup_used"
REJECTED_NO_NAME = "rejected_no_name"
REJECTED_MID_SENTENCE = "rejected_mid_sentence"
REJECTED_POSSESSIVE = "rejected_possessive"
REJECTED_CONTINUATION = "rejected_continuation"
REJECTED_LOW_CONF = "rejected_low_conf"
REJECTED_UNCONFIRMED = "rejected_unconfirmed"  # without the hint, the phrase was gone
HITS = frozenset({ACCEPTED_WITH_COMMAND, ACCEPTED_ALONE, FOLLOWUP_USED})

_APOSTROPHES = ("'", chr(0x2019))  # straight and typographic (right single quote)


@dataclass(frozen=True)
class WakeDecision:
    outcome: str
    command: str | None = None  # set only when a turn should run
    variant: int = -1  # which spelling matched (0 = the phrase itself); for telemetry
    phrase_p: float | None = None  # the name's word probability (a number; telemetry)
    rechecked: bool = False  # decoded again without the hint (a doubtful wake)

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
        word_variants: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        """`phrase` may be several words ("hey <name>"). `word_variants` gives other
        spellings per word; any mix of them matches (form 1). Form 0 is the phrase as
        written, forms 2+ are whole-phrase `variants`: the index goes to telemetry."""
        self.phrase = phrase  # also the decoder's hotword for wake segments
        exact = _words_pattern(phrase)
        if not exact:
            raise ValueError("wake_word.phrase has no words")
        spelled = {k.lower(): list(v) for k, v in (word_variants or {}).items()}
        words = re.findall(r"\w+", phrase.lower())
        mixed = r"\W+".join(
            "(?:" + "|".join(f for f in map(_words_pattern, [w, *spelled.get(w, [])]) if f) + ")"
            for w in words
        )
        forms = [exact, mixed if any(spelled.get(w) for w in words) else r"(?!)"]
        self.forms = forms + [f for f in map(_words_pattern, variants) if f]
        filler = "|".join(re.escape(f.lower()) for f in fillers) or r"(?!)"
        self._lead = rf"^\W*(?:(?:{filler})\W+)*"
        self._continuations = {c.lower() for c in continuations}
        self._fillers = {f.lower() for f in fillers}
        self._words = words

    def opens_like_phrase(self, text: str) -> bool:
        """The no-hint check (false wakes, 2026-11-16): `text`, decoded without the hint,
        still holds the phrase at the start, or at least its first word ("hey") when
        the phrase has several: a name the decoder doesn't know may come out as anything
        without the hint, but "hey" doesn't need one."""
        if self.match(text)[0] in (ACCEPTED_ALONE, ACCEPTED_WITH_COMMAND):
            return True
        if len(self._words) < 2:
            return False
        said = re.findall(r"\w+", text.lower())
        skip = self._fillers - {self._words[0]}
        while said and said[0] in skip:
            said.pop(0)
        return bool(said) and said[0] == self._words[0]

    def phrase_tokens(self, text: str) -> tuple[int, int] | None:
        """Where the phrase sits at the start of `text`, in word tokens (\\w+): how many
        come before it (fillers) and how many it has. None if it isn't at the start."""
        for form in self.forms:
            m = re.match(rf"({self._lead})({form})(?!\w)", text, re.IGNORECASE)
            if m:
                return len(re.findall(r"\w+", m.group(1))), len(re.findall(r"\w+", m.group(2)))
        return None

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
            # "<name> is coming over" talks about the name; "<name>, is the light on?"
            # addresses it. Only a comma, "!" or "?" right after the name counts as
            # addressing (T2): the transcriber writes "." and "..." for any pause, and
            # "<name>... was here" or "<name> - is coming" still talk about the name.
            addressed = re.match(r"\s*[,!?]", rest) is not None
            first = re.match(r"\w+", command.lower())
            if not addressed and first and first.group(0) in self._continuations:
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
        min_command_confidence: float = 0.0,
        sure_command_confidence: float = 0.0,
        recheck_from_s: float = 0.0,
    ) -> None:
        """The command bars default to off (the gate before 2026-11-16); the listener
        passes the profile's (wake_open.py)."""
        self.matcher = matcher
        self.armed_window_s = armed_window_s
        self.min_confidence = min_confidence
        self.min_command_confidence = min_command_confidence
        self.sure_command_confidence = sure_command_confidence
        self.recheck_from_s = recheck_from_s
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

    def _doubtful(self, confidence: float | None, seconds: float | None) -> bool:
        if confidence is None or confidence >= self.sure_command_confidence:
            return False
        return seconds is None or seconds >= self.recheck_from_s

    def needs_recheck(self, text: str, confidence: float | None, seconds: float) -> bool:
        """A fresh "phrase, command" in the doubtful band: decode it again without the
        hint before deciding (wake_evidence.hear)."""
        if self.armed or self.matcher.match(text)[0] != ACCEPTED_WITH_COMMAND:
            return False
        low = confidence is not None and confidence < self.min_command_confidence
        return not low and self._doubtful(confidence, seconds)

    def decide(
        self,
        text: str,
        confidence: float | None,
        words: Sequence[Word] = (),
        seconds: float | None = None,
        plain: str | None = None,
    ) -> WakeDecision:
        """`words`: the decoder's per-word probabilities (wake segments have them);
        `seconds`: the segment's length; `plain`: the no-hint decode of a doubtful one."""
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
        p = None
        if outcome in (ACCEPTED_ALONE, ACCEPTED_WITH_COMMAND):
            ev = phrase_evidence(text, words, self.matcher.phrase_tokens(text))
            p = None if ev is None else ev.probability
        if outcome == ACCEPTED_ALONE:
            if confidence is not None and confidence < self.min_confidence:
                return WakeDecision(REJECTED_LOW_CONF, variant=variant, phrase_p=p)
            self.armed_until = self.clock() + self.armed_window_s
        elif outcome == ACCEPTED_WITH_COMMAND:
            if confidence is not None and confidence < self.min_command_confidence:
                return WakeDecision(REJECTED_LOW_CONF, variant=variant, phrase_p=p)
            if self._doubtful(confidence, seconds):
                if plain is None or not self.matcher.opens_like_phrase(plain):
                    return WakeDecision(REJECTED_UNCONFIRMED, variant=variant, phrase_p=p)
                return WakeDecision(outcome, command, variant, phrase_p=p, rechecked=True)
        return WakeDecision(outcome, command, variant, phrase_p=p)


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
