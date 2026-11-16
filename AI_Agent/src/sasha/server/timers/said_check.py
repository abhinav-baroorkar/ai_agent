"""The timer tools' checks against the owner's words, and which words count (said.py).

The words come from the conversation's turn context (`sasha.conversation`): the owner's
own messages, the current one last. An answer is read with the message it answers:
- a timer tool asked back ("7:00 in the morning or in the evening?") and the person
  answers with only the missing part ("evening") (S1);
- or the assistant asked by itself (its reply ended in "?"), the previous message was
  a timer request, and that turn made no item ("Set an alarm for 7." / "Morning.").
Only when the current message is an answer fragment: one with a request word of its
own ("remind me to call mom") is a new request, read alone (B2). And only within
`answer_window_s` of the question. On the wake path anyone in earshot could answer an
open question within that window; the window and the name requirement bound that.
Only the fact that a turn asked is kept here, never its text beyond a comparison.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sasha.config_timers import TimeCues
from sasha.conversation import TurnContext
from sasha.server.timers.resolve import Ask, whole
from sasha.server.timers.said import Said, tokens

UserTurn = Callable[[], TurnContext | None]  # real callers: `current_turn.get`
MAX_SPAN = 3  # at most the asked turn, one answer, and one more answer
_HALF_PARTS = (("half", (30, 1800)), ("quarter", (15, 45, 900, 2700)))


@dataclass(frozen=True)
class _Asked:
    turn_id: int
    count: int  # how many user messages the conversation had then
    last: str  # that turn's message, to be sure the next turn follows it
    span: int  # how many messages that turn's words covered


class SaidCheck:
    """One per conversation's timer tools."""

    def __init__(
        self, said: UserTurn | None, cues: Iterable[TimeCues], window_s: float = 120.0
    ) -> None:
        self.said = said
        self.cues = list(cues)
        self.window_s = window_s
        self.request_words = {w.lower() for c in self.cues for w in c.request_words}
        self._asked: _Asked | None = None  # the last turn in which a tool asked back
        self._made_count = -1  # user messages the conversation had when an item was made

    def _is_request(self, message: str) -> bool:
        return any(w in self.request_words for w in tokens(message))

    def _span(self, turn: TurnContext) -> int:
        """How many of the latest user messages are this turn's words (see above)."""
        msgs, asked = turn.user_messages, self._asked
        if asked is not None and turn.turn_id == asked.turn_id:
            return asked.span  # a second call in the same turn
        # None: the conversation's first turn (the orchestrator sets it on every other).
        fresh = turn.since_previous_s is None or turn.since_previous_s <= self.window_s
        if not fresh or len(msgs) < 2 or self._is_request(msgs[-1]):
            return 1  # stale, nothing before, or a new request of its own (B2)
        if asked is not None and len(msgs) == asked.count + 1 and msgs[-2] == asked.last:
            return min(asked.span + 1, MAX_SPAN)
        made_item_before = self._made_count == len(msgs) - 1
        if turn.previous_reply_asked and not made_item_before and self._is_request(msgs[-2]):
            return 2  # the one previous message (a timer request), plus this answer
        return 1

    def _text(self) -> str | None:
        if self.said is None:
            return None  # direct calls in tests only
        turn = self.said()
        if turn is None or not turn.user_messages:
            return ""  # not in a turn: nothing was said, so every time asks
        return "\n".join(turn.user_messages[-self._span(turn) :])  # one message per line

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
        """(c) a delay must be said, and its value must be one said (B3); a "half" or
        "quarter" said must be in it ("two and a half minutes" sent as 2 minutes)."""
        words = self.words(*content)
        if words is None:
            return
        if not words.has_delay():
            raise Ask("The person didn't say how long. Ask.", "not_said")
        if not words.delay_said(total_s):
            raise Ask("That length isn't the one the person said. Ask how long.", "not_said")
        for word, parts in _HALF_PARTS:
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
