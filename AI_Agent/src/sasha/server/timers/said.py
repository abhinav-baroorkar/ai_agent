"""Was it said? Checks the timer tools' fields against the person's own words.

The bench showed the model filling in fields nobody said: an "am" for "at 1", a
"15 minutes" for "remind me to check the oven". The fields alone can't show that, so
the tools also get the turn's user text (the typed message or the wake command) and:
- count a number only where it stands as a time (B1, B3): a clock time after "at",
  "by"... or before am/pm, "o'clock", ":mm" or a period word; a delay after "in" or
  "for", or before a duration word; or anywhere in a message that is only a time.
  "Remind me to take 2 pills" has no time, however the model words the reminder;
- drop a `period` the words give no cue for; follow the person's one cue when the
  model's period disagrees; ask when the cues disagree (a, S3);
- read a 13-23 (or 0) hour the words don't contain as its 12-hour form (a);
- refuse a clock time with no time said (b), or whose hour isn't one said (S5);
- refuse a delay with no delay said (c), or whose value isn't one said (B3), and a
  "half" or "quarter" said must be in it.
The model's own content (the reminder text, a label) is also taken out of the words
first, as a second layer (S2, S4). Cue words come from config, per language; a cue from
any configured language counts. The text is only read: never stored, logged or sent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sasha.config_timers import TimeCues

_SPLIT = re.compile(r"(?<=\d)(?=[^\d\s])|(?<=[^\d\s:])(?=\d)")  # "7am" -> "7 am"
_HHMM = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")  # "6:15", "7.30"
_LABEL_FILLER = {"a", "an", "and", "the", "for", "of", "timer", "alarm", "reminder"}


def tokens(text: str) -> list[str]:
    text = text.lower().replace(chr(0x2019), "'")
    text = _HHMM.sub(r"\1 :\2", text)  # "6:15" -> "6", ":15" (the minutes, marked)
    text = _SPLIT.sub(" ", text)  # "7a.m." -> "7 a.m."
    text = re.sub(r"\b([ap])\.\s?m\b\.?", r"\1m", text)  # "a.m." -> "am"
    return re.findall(r":\d+|[\w']+", text)


def without(words: list[str], content: list[str]) -> list[str]:
    """`words` minus `content`: the first contiguous run if there is one, else each
    content word once (fails safe: a real time said twice may be lost, never added)."""
    n = len(content)
    if n:
        for i in range(len(words) - n + 1):
            if words[i : i + n] == content:
                return words[:i] + words[i + n :]
    rest = list(words)
    for word in content:
        if word in rest:
            rest.remove(word)
    return rest


def _vocab(cues: list[TimeCues], field: str) -> set[str]:
    return {w.lower() for c in cues for w in getattr(c, field)}


class Said:
    """One check's view of the words: the clock hours and delays said, and the cues.

    `text` may hold several messages, one per line (an answer with its question)."""

    def __init__(self, text: str, cues: Iterable[TimeCues], content: Iterable[str] = ()) -> None:
        cue_list = list(cues)
        self.am_words, self.pm_words = _vocab(cue_list, "am_words"), _vocab(cue_list, "pm_words")
        self.time_words = _vocab(cue_list, "time_words")
        self.duration_words = _vocab(cue_list, "duration_words")
        self.fractions = _vocab(cue_list, "fraction_words")
        self.values = {k.lower(): v for c in cue_list for k, v in c.number_values.items()}
        self.clock_before = _vocab(cue_list, "clock_before")
        self.delay_before = _vocab(cue_list, "delay_before")
        self.gap = _vocab(cue_list, "period_gap")
        self.before_words = _vocab(cue_list, "before_words")
        self.filler = _vocab(cue_list, "filler_words")
        self.words: list[str] = []
        self.hours: set[int] = set()  # clock hours said (as said: 7, 19, 12 ...)
        self.delays: set[int] = set()  # delay values said, in their own unit
        for line in text.splitlines() or [""]:
            said = tokens(line)
            self._read(said)  # positions as said: removing content can join words
            words = said
            for part in content:
                words = without(words, tokens(part))
            self.words += words  # cue words outside the model's own content (S4)

    # --- reading one message -------------------------------------------------------
    def _numbers(self, words: list[str]) -> list[tuple[int, int, int]]:
        """(value, first index, last index) of each number: digits, or number words
        added up ("twenty five")."""
        found: list[tuple[int, int, int]] = []
        i = 0
        while i < len(words):
            w = words[i]
            if w.isdigit() and len(w) <= 4:
                found.append((int(w), i, i))
            elif w in self.values:
                value, j = self.values[w], i
                if value >= 20 and value % 10 == 0 and i + 1 < len(words):
                    unit = self.values.get(words[i + 1])
                    if unit is not None and 0 < unit < 10:
                        value, j = value + unit, i + 1
                found.append((value, i, j))
                i = j
            i += 1
        return found

    def _only_time(self, words: list[str]) -> bool:
        known = self.am_words | self.pm_words | self.time_words | self.duration_words
        known |= self.fractions | self.filler
        return bool(words) and all(
            w in known or w in self.values or w.isdigit() or w.startswith(":") for w in words
        )

    def _read(self, words: list[str]) -> None:
        only_time = self._only_time(words)
        for value, first, last in self._numbers(words):
            back = first - 1
            if back > 0 and words[back] in ("a", "an", "the"):
                back -= 1  # "in a couple of hours"
            before = words[back] if back >= 0 else ""
            after = [w for w in words[last + 1 : last + 5] if w != "of"][:3]
            nxt = after[0] if after else ""
            period_after = next((w for w in after if w not in self.gap), "")
            clock = (
                before in self.clock_before
                or nxt.startswith(":")
                or nxt in self.time_words
                or period_after in self.am_words | self.pm_words
            )
            delay = before in self.delay_before or nxt in self.duration_words
            if nxt == "and" and len(after) > 2 and after[1] in ("a", "an"):
                delay = delay or (len(after) > 3 and after[3] in self.duration_words)
            if only_time:
                clock = delay = True
            if clock and value <= 23:
                self.hours.add(value)
            if delay:
                self.delays.add(value)
        for i, w in enumerate(words):  # "an hour", "a minute": one
            if w in self.duration_words and i and words[i - 1] in ("a", "an"):
                self.delays.add(1)

    # --- what the checks ask --------------------------------------------------------
    def _has(self, vocabulary: set[str]) -> bool:
        return any(w in vocabulary for w in self.words)

    def periods(self) -> set[str]:
        """The readings the words give: "am", "pm", both, or none. A 13-23 hour is pm."""
        found = set()
        if self._has(self.am_words):
            found.add("am")
        if self._has(self.pm_words) or any(13 <= h <= 23 for h in self.hours):
            found.add("pm")
        return found

    def has_clock_time(self) -> bool:
        return bool(self.hours) or self._has(self.time_words)

    def has_delay(self) -> bool:
        return bool(self.delays) or self._has(self.fractions)

    def says_hour(self, hour: int) -> bool:
        """The 24-hour value itself was said as a time ("19:00")."""
        return hour in self.hours

    def hour_said(self, hour: int) -> bool:
        """S5: the hour (12-hour form) is one said as a time, or the next one after a
        "to" word ("quarter to eight" is 7:45); noon and midnight are 12."""
        said = {h % 12 or 12 for h in self.hours}
        if self._has({"noon", "midday", "midnight"}):
            said.add(12)
        h12 = hour % 12 or 12
        if h12 in said:
            return True
        return self._has(self.before_words) and (h12 % 12 + 1) in said

    def delay_said(self, total_s: int) -> bool:
        """B3: the delay is a value said, in hours, minutes or seconds ("90 seconds");
        or its whole hours or minutes are, with the rest said too ("an hour and twenty
        minutes" sent as 80 minutes; "an hour and a half"); or it is a half or quarter
        said on its own ("half an hour")."""
        if any(total_s % u == 0 and total_s // u in self.delays for u in (3600, 60, 1)):
            return True
        fraction = self._has(self.fractions)
        unit = 3600 if total_s >= 3600 else 60 if total_s >= 60 else 1
        whole_part, rest = divmod(total_s, unit)
        sub = unit // 60 if unit > 1 else 1
        parts = (unit // 2, unit // 4, 3 * unit // 4)
        rest_said = (rest % sub == 0 and rest // sub in self.delays) or (fraction and rest in parts)
        if whole_part in self.delays and rest_said:
            return True
        return fraction and total_s in (30, 15, 45, 1800, 900, 2700)

    def only_time_words(self, text: str) -> bool:
        """A label that only restates the time ("ten minutes"): not content at all."""
        known = self.duration_words | self.time_words | self.fractions | _LABEL_FILLER
        known |= self.am_words | self.pm_words
        words = tokens(text)
        return bool(words) and all(w in known or w in self.values or w.isdigit() for w in words)
