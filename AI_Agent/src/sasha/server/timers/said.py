"""Was it said? Checks the timer tools' fields against the person's own words.

The bench showed the model filling in fields nobody said: an "am" for "at 1", a
"15 minutes" for "remind me to check the oven". The fields alone can't show that, so
the tools also get the turn's user text (the typed message or the wake command) and:
- drop a `period` the words give no cue for, and read a 13-23 (or 0) hour the words
  don't contain as its 12-hour form, so the ask rules apply (a);
- refuse a clock time when the words have no number and no time word (b);
- refuse a delay when the words have no number and no duration word (c).
Cue words come from config, per language; a cue from any configured language counts.
The text is only read here: never stored, logged or sent to telemetry.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sasha.config_timers import TimeCues

_SPLIT = re.compile(r"(?<=\d)(?=[^\d\s])|(?<=[^\d\s:])(?=\d)")  # "7am" -> "7 am"


def _tokens(text: str) -> list[str]:
    text = text.lower().replace(chr(0x2019), "'")
    text = re.sub(r"\b([ap])\.\s?m\b\.?", r"\1m", text)  # "a.m." -> "am"
    text = _SPLIT.sub(" ", text)
    return re.findall(r"[\w']+", text)


class Said:
    def __init__(self, text: str, cues: Iterable[TimeCues]) -> None:
        self.words = _tokens(text)
        cue_list = list(cues)
        self.period_words = {w.lower() for c in cue_list for w in c.period_words}
        self.number_words = {w.lower() for c in cue_list for w in c.number_words}
        self.time_words = {w.lower() for c in cue_list for w in c.time_words}
        self.duration_words = {w.lower() for c in cue_list for w in c.duration_words}
        self.numbers = {int(w) for w in self.words if w.isdigit() and len(w) <= 4}

    def _has(self, vocabulary: set[str]) -> bool:
        return any(w in vocabulary for w in self.words)

    def has_number(self) -> bool:
        return bool(self.numbers) or self._has(self.number_words)

    def has_period(self) -> bool:
        """am/pm/morning/tonight/... or a 24-hour number (13-23) in the words."""
        return self._has(self.period_words) or any(13 <= n <= 23 for n in self.numbers)

    def has_clock_time(self) -> bool:
        return self.has_number() or self._has(self.time_words)

    def has_delay(self) -> bool:
        return self.has_number() or self._has(self.duration_words)

    def says_hour(self, hour: int) -> bool:
        """The hour itself is in the words as digits ("19:00", "00:30")."""
        return hour in self.numbers
