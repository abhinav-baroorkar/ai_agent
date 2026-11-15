"""What a tool may know about the turn it runs in, without reaching into the Assistant.

`Assistant.handle_text` sets `current_turn` for the duration of a turn (tools run
synchronously inside it, on the same thread, so they see it). Tools that must check
where an input came from (the browser's `open_url`, review condition B1) read the
owner's own messages from here: user messages only, never tool results or the model's
text.
"""

from __future__ import annotations

import contextvars
import itertools
from dataclasses import dataclass

_turn_ids = itertools.count(1)


@dataclass(frozen=True)
class TurnContext:
    user_messages: tuple[str, ...]  # this conversation's user messages, the current one last
    input_mode: str  # "text", "voice" (window) or "wake"
    turn_id: int
    # Did the assistant's previous reply in this conversation end in a question? Only
    # the fact, never the reply's text (the timers' said-check uses it to read an
    # answer such as "Morning." with the question's turn).
    previous_reply_asked: bool = False
    since_previous_s: float | None = None  # seconds since the previous turn ended


current_turn: contextvars.ContextVar[TurnContext | None] = contextvars.ContextVar(
    "current_turn", default=None
)


def new_turn(
    user_messages: tuple[str, ...],
    input_mode: str,
    previous_reply_asked: bool = False,
    since_previous_s: float | None = None,
) -> TurnContext:
    return TurnContext(
        user_messages, input_mode, next(_turn_ids), previous_reply_asked, since_previous_s
    )
