"""Was it said? Checks the timer tools' fields against the person's own words.

The bench showed the model filling in fields nobody said: an "am" for "at 1", a
"15 minutes" for "remind me to check the oven". The fields alone can't show that, so
the tools also get the turn's user text (the typed message or the wake command) and:
- drop a `period` the words give no cue for; follow the cue when the model's period
  disagrees with the one cue said; ask when the cues disagree (a, S3);
- read a 13-23 (or 0) hour the words don't contain as its 12-hour form (a);
- refuse a clock time when the words have no number and no time word (b), or when
  its hour isn't one of the numbers said ("at 7" sent as 8; "quarter to eight" may be
  7:45) (S5);
- refuse a delay when the words have no number and no duration word (c).
The model's own content (the reminder text, a label) is taken out of the words first,
so "take 2 pills" or "say good night" can't count as a time or a period (S2, S4).
Cue words come from config, per language; a cue from any configured language counts.
The text is only read here: never stored, logged or sent to telemetry.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sasha.config_timers import TimeCues
from sasha.conversation import TurnContext
from sasha.server.timers.resolve import Ask, whole

UserTurn = Callable[[], TurnContext | None]  # real callers: `current_turn.get`
MAX_SPAN = 3  # at most the asked turn, one answer, and one more answer


@dataclass(frozen=True)
class _Asked:
    turn_id: int
    count: int  # how many user messages the conversation had then
    last: str  # that turn's message, to be sure the next turn follows it
    span: int  # how many messages that turn's words covered


_SPLIT = re.compile(r"(?<=\d)(?=[^\d\s])|(?<=[^\d\s:])(?=\d)")  # "7am" -> "7 am"
_MINUTES = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")  # "6:15", "7.30"
_FILLER = {"a", "an", "and", "the", "for", "of", "timer", "alarm", "reminder"}


def tokens(text: str) -> list[str]:
    text = text.lower().replace(chr(0x2019), "'")
    text = _SPLIT.sub(" ", text)  # first, so "7a.m." becomes "7 a.m."
    text = re.sub(r"\b([ap])\.\s?m\b\.?", r"\1m", text)  # "a.m." -> "am"
    return re.findall(r"[\w']+", text)


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


class Said:
    def __init__(self, text: str, cues: Iterable[TimeCues], content: Iterable[str] = ()) -> None:
        words = tokens(text)
        for part in content:
            words = without(words, tokens(part))
        self.words = words
        cue_list = list(cues)
        self.am_words = {w.lower() for c in cue_list for w in c.am_words}
        self.pm_words = {w.lower() for c in cue_list for w in c.pm_words}
        self.number_words = {w.lower() for c in cue_list for w in c.number_words}
        self.time_words = {w.lower() for c in cue_list for w in c.time_words}
        self.duration_words = {w.lower() for c in cue_list for w in c.duration_words}
        self.before_words = {w.lower() for c in cue_list for w in c.before_words}
        hour_words = {k.lower(): v for c in cue_list for k, v in c.hour_words.items()}
        # The minutes of "6:15" are no hour: they don't count as a 13-23 hour (a pm cue)
        # or as an hour said. Found by the bench ("At 6:15." with am became 18:15).
        digits = [w for w in words if w.isdigit() and len(w) <= 4]
        for minutes in _MINUTES.findall(text):
            if minutes[1] in digits:
                digits.remove(minutes[1])
        self.numbers = {int(w) for w in digits}
        self.hours = {n % 12 or 12 for n in self.numbers if n <= 23}
        self.hours |= {hour_words[w] for w in words if w in hour_words}

    def _has(self, vocabulary: set[str]) -> bool:
        return any(w in vocabulary for w in self.words)

    def has_number(self) -> bool:
        return bool(self.numbers) or self._has(self.number_words)

    def periods(self) -> set[str]:
        """The readings the words give: "am", "pm", both, or none. A 13-23 number is pm."""
        found = set()
        if self._has(self.am_words):
            found.add("am")
        if self._has(self.pm_words) or any(13 <= n <= 23 for n in self.numbers):
            found.add("pm")
        return found

    def has_clock_time(self) -> bool:
        return self.has_number() or self._has(self.time_words)

    def has_delay(self) -> bool:
        return self.has_number() or self._has(self.duration_words)

    def says_hour(self, hour: int) -> bool:
        """The 24-hour value itself is in the words as digits ("19:00")."""
        return hour in self.numbers

    def hour_said(self, hour: int) -> bool:
        """S5: the hour (12-hour form) is one of the numbers said, or the next one after
        a "to" word ("quarter to eight" is 7:45)."""
        h12 = hour % 12 or 12
        if h12 in self.hours or hour in self.numbers:
            return True
        return self._has(self.before_words) and (h12 % 12 + 1) in self.hours

    def only_time_words(self, text: str) -> bool:
        """A label that only restates the time ("ten minutes"): not content at all."""
        known = self.number_words | self.duration_words | self.time_words | _FILLER
        known |= self.am_words | self.pm_words
        words = tokens(text)
        return bool(words) and all(w in known or w.isdigit() for w in words)


class SaidCheck:
    """One per conversation's timer tools: the checks, and which turn asked back (S1).

    The words come from the conversation's turn context (`sasha.conversation`): the
    owner's own messages, the current one last. When a tool asks back ("7:00 in the
    morning or in the evening?"), the person answers with only the missing part
    ("evening"), so the next turn is checked with the asked turn's message too. Only
    the fact that a turn asked is kept here, never its text beyond a comparison; it
    ends once an item is made, or when a later turn isn't the answer.
    """

    def __init__(
        self, said: UserTurn | None, cues: Iterable[TimeCues], window_s: float = 120.0
    ) -> None:
        self.said = said
        self.cues = list(cues)
        self.window_s = window_s  # an answer must follow its question within this
        self._asked: _Asked | None = None  # the last turn in which a tool asked back
        self._made_count = -1  # user messages the conversation had when an item was made

    def _span(self, turn: TurnContext) -> int:
        """How many of the latest user messages are this turn's words: 1, or more when
        this turn answers a question asked in the turn just before, at most
        `answer_window_s` ago: one a timer tool asked (S1), or one the assistant asked
        by itself (its reply ended in "?") in a turn that made no item."""
        msgs, asked = turn.user_messages, self._asked
        if asked is not None and turn.turn_id == asked.turn_id:
            return asked.span  # a second call in the same turn
        fresh = turn.since_previous_s is None or turn.since_previous_s <= self.window_s
        if not fresh or len(msgs) < 2:
            return 1  # a stale question lends nothing to a new request
        if asked is not None and len(msgs) == asked.count + 1 and msgs[-2] == asked.last:
            return min(asked.span + 1, MAX_SPAN)
        made_item_before = self._made_count == len(msgs) - 1
        if turn.previous_reply_asked and not made_item_before:
            return 2  # the one previous message, plus this one
        return 1

    def _text(self) -> str | None:
        if self.said is None:
            return None  # direct calls in tests only
        turn = self.said()
        if turn is None or not turn.user_messages:
            return ""  # not in a turn: nothing was said, so every time asks
        return " ".join(turn.user_messages[-self._span(turn) :])

    def words(self, *content: str) -> Said | None:
        """None only for callers with no user text (direct calls in tests)."""
        text = self._text()
        return None if text is None else Said(text, self.cues, content)

    def asked(self) -> None:
        """A tool asked back: the next turn's words are checked with this turn's (S1)."""
        turn = self.said() if self.said is not None else None
        if turn is not None and turn.user_messages:
            msgs = turn.user_messages
            self._asked = _Asked(turn.turn_id, len(msgs), msgs[-1], self._span(turn))

    def done(self) -> None:
        """An item was made: nothing is carried on from this turn."""
        self._asked = None
        turn = self.said() if self.said is not None else None
        self._made_count = len(turn.user_messages) if turn is not None else -1

    def label(self, label: str) -> str:
        """A label that only restates the time is dropped (it isn't content)."""
        return "" if label and Said("", self.cues).only_time_words(label) else label

    def delay(self, total_s: int, *content: str) -> None:
        """(c) a delay needs a number or a duration word, outside the content; and a
        "half" or "quarter" said must be in it ("two and a half minutes" sent as 2
        minutes, found by the bench)."""
        words = self.words(*content)
        if words is None:
            return
        if not words.has_delay():
            raise Ask("The person didn't say how long. Ask.", "not_said")
        for word, parts in (("half", (30, 1800)), ("quarter", (15, 45, 900, 2700))):
            has_part = total_s % 60 in parts or total_s % 3600 in parts
            if word in words.words and not has_part:
                raise Ask(f"The {word} the person said is missing. Ask how long.", "not_said")

    def clock(self, hour: Any, period: Any, *content: str) -> tuple[Any, Any]:
        """(b), S5, (a), S3: the (hour, period) the words support, else ask."""
        words = self.words(*content)
        if words is None or hour in (None, ""):
            return hour, period
        if not words.has_clock_time():
            raise Ask("The person didn't say a time. Ask when.", "not_said")
        h = whole(hour, "hour", 0, 23)
        if not words.hour_said(h):
            raise Ask("That hour isn't the one the person said. Ask for the time.", "not_said")
        cues = words.periods()
        if len(cues) > 1:
            raise Ask(
                f"{h % 12 or 12}:00 in the morning or in the evening? Ask.", "ambiguous_period"
            )
        cue = next(iter(cues), None)
        if h >= 13 or h == 0:
            if words.says_hour(h) or cue == ("pm" if h >= 13 else "am"):
                return h, ""  # "19:00", or "8 tonight" sent as 20
            if cue is None:
                return (h - 12 if h >= 13 else 12), ""  # the 12-hour reading: ask rules
            raise Ask(f"{h % 12 or 12} in the morning or in the evening? Ask.", "ambiguous_period")
        return h, cue or ""  # S3: the person's own cue wins over the model's period
