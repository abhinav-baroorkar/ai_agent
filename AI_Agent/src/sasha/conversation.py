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
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

_turn_ids = itertools.count(1)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnContext:
    user_messages: tuple[str, ...]  # this conversation's user messages, the current one last
    input_mode: str  # "text", "voice" (window) or "wake"
    turn_id: int
    # The turn's own stop (the Stop button), so a helper inside a tool can see it too.
    should_stop: Callable[[], bool] | None = field(default=None, compare=False)


current_turn: contextvars.ContextVar[TurnContext | None] = contextvars.ContextVar(
    "current_turn", default=None
)


def new_turn(
    user_messages: tuple[str, ...],
    input_mode: str,
    should_stop: Callable[[], bool] | None = None,
) -> TurnContext:
    return TurnContext(user_messages, input_mode, next(_turn_ids), should_stop)


# Which bot a conversation's tools are being built for (set by `make_assistant`), so a
# connector can tell whose conversation it serves (the browser live view's holder_bot).
building_for_bot: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "building_for_bot", default=None
)


# Things to do when a turn ends (the browser's "working" state lasts the whole turn).
_end_hooks: dict[int, list[Callable[[], None]]] = {}
_hooks_lock = threading.Lock()


def on_turn_end(turn_id: int, hook: Callable[[], None]) -> None:
    with _hooks_lock:
        _end_hooks.setdefault(turn_id, []).append(hook)


def finish_turn(turn_id: int) -> None:
    """Run and forget the turn's end hooks (called by `Assistant.handle_text`)."""
    with _hooks_lock:
        hooks = _end_hooks.pop(turn_id, [])
    for hook in hooks:
        try:
            hook()
        except Exception as exc:  # a hook must never break the turn
            log.warning("Turn end hook failed: %s", type(exc).__name__)
