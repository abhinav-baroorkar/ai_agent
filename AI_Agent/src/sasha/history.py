"""A conversation's history: the kept turns, their records, and what may change them
(split out of orchestrator.py). One record per kept turn, filtered together with the
two history entries under one lock (review E4); writes after the turn go by identity
(memory W3); a forgotten window also drops a turn still in flight (memory W2)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from sasha.config import Settings
from sasha.conversation import asks_back
from sasha.pipeline.llm import Message

EARLIER_TURNS = 2  # the owner's messages that may name a change's target


@dataclass
class _Turn:
    """One kept turn's record, alongside its two history entries (review E4): when it
    started and ended (the turn clock), and whether it brought in outside text."""

    started: float
    ended: float
    browsed: bool


class HistoryMixin:
    """The Assistant's history side. Set up by `_init_history`; needs `settings`,
    `clock` and `history_turns` from the Assistant."""

    settings: Settings
    clock: Callable[[], float]
    history_turns: int

    def _init_history(self) -> None:
        self._history: list[Message] = []
        self._turns: list[_Turn] = []  # per kept turn, filtered with `_history` (E4)
        # forget_turns may come from another thread (the Memory screen) mid-turn.
        self._history_lock = threading.Lock()
        # The latest forgotten window (start, when it was forgotten): a turn that started
        # inside it but ended after the forget is dropped when it lands (memory W2).
        self._forgot: tuple[float, float] | None = None
        self._this_turn: tuple[Message, Message] | None = None  # this turn's two entries
        self._turn_browsed = False  # did this turn bring in outside text?
        self._last_turn_end: float | None = None
        self.turn_kept = False  # the last turn is in the history (see replace_reply)

    def previous_reply_asked(self) -> bool:
        """Did the last reply ask the owner something? Only the fact. A question whose
        speech was cut isn't counted: the answer is read alone and asks again (the safe
        failure, by design)."""
        replies = [m["content"] for m in self._history if m["role"] == "assistant"]
        last = str(replies[-1]) if replies else ""
        return asks_back(last) and not last.endswith(self.settings.profile.llm.interrupted_note)

    def previous_request(self) -> str | None:
        """The owner's previous message in this conversation (None at the start)."""
        said = [m["content"] for m in self._history if m["role"] == "user"]
        return str(said[-1]) if said else None

    def since_previous(self) -> float | None:
        """Seconds since the previous turn ended (None at the start)."""
        return None if self._last_turn_end is None else self.clock() - self._last_turn_end

    def reset(self) -> None:
        with self._history_lock:
            self._history.clear()
            self._turns.clear()
            self._this_turn = None
        self._last_turn_end = None

    @property
    def tainted(self) -> bool:
        """A turn still in the model's context brought in outside text (P1)."""
        recent = self._turns[-self.history_turns :] if self.history_turns > 0 else []
        return any(t.browsed for t in recent)

    def forget_turns(self, seconds: float) -> int:
        """Drop this conversation's turns that started in the last `seconds` (memory's
        forget(minutes)): the model can't quote them any more. A turn still running is
        dropped when it lands (W2). Returns how many went now."""
        now = self.clock()
        since = now - seconds
        with self._history_lock:
            keep = [i for i, t in enumerate(self._turns) if t.started < since]
            gone = len(self._turns) - len(keep)
            self._history = [m for i in keep for m in self._history[2 * i : 2 * i + 2]]
            self._turns = [self._turns[i] for i in keep]
            self._forgot = (since, now)
        return gone

    def _kept(self, entry: Message) -> bool:
        return any(m is entry for m in self._history)

    def redact_request(self, redact: Callable[[str], str], count: int = 1) -> None:
        """Rewrite the owner's last `count` messages as kept in the history (a refused
        secret, memory M7: this turn's, and the asked-back one before it). By identity,
        under the lock: never another turn's entry (W3)."""
        with self._history_lock:
            if self._this_turn is None or not self._kept(self._this_turn[0]):
                return
            at = next(i for i, m in enumerate(self._history) if m is self._this_turn[0])
            users = [m for m in self._history[: at + 1] if m["role"] == "user"][-count:]
            for message in users:
                message["content"] = redact(str(message["content"]))

    def _keep(self, text: str, reply: str, started: float) -> None:
        """Keep this turn in the history, unless a forget covered its start (W2)."""
        with self._history_lock:
            self._last_turn_end = ended = self.clock()
            if self._forgot is not None and self._forgot[0] <= started <= self._forgot[1]:
                self._this_turn, self.turn_kept = None, False
                return
            pair: tuple[Message, Message] = (
                {"role": "user", "content": text},
                {"role": "assistant", "content": reply},
            )
            self._history += list(pair)
            self._turns.append(_Turn(started, ended, self._turn_browsed))
            self._this_turn, self.turn_kept = pair, True

    # --- stopped turns ---------------------------------------------------------------
    # A stopped turn (the Stop button, or the owner talking over the reply) stays in the
    # history: the request, what was said of the reply (capped), and a short note only
    # the model sees. So "continue" or "go on" picks it up. Telemetry only records that
    # it was cancelled, never the text.
    def _stopped_reply(self, partial: str) -> str:
        cfg = self.settings.profile.llm
        kept = partial.strip()[: cfg.interrupted_keep_chars]
        return f"{kept} {cfg.interrupted_note}".strip()

    def _keep_stopped(self, text: str, partial: str, started: float) -> None:
        self._keep(text, self._stopped_reply(partial), started)

    def replace_reply(self, said: str, stopped: bool = False) -> None:
        """The history keeps what the person was really shown and told, not the model's
        raw text: the socket's released text (a held-back false claim, markdown taken
        out of speech). Only this turn's entry: nothing if the turn wasn't kept."""
        with self._history_lock:  # by identity: never another turn's entry (W3)
            if self.turn_kept and self._this_turn and self._kept(self._this_turn[1]):
                self._this_turn[1]["content"] = self._stopped_reply(said) if stopped else said

    def mark_interrupted(self) -> None:
        """The last reply was finished but its speech was cut: the model should know
        the person may not have heard all of it."""
        note = self.settings.profile.llm.interrupted_note
        if self._history and self._history[-1]["role"] == "assistant":
            last = self._history[-1]
            if not str(last["content"]).endswith(note):
                last["content"] = f"{str(last['content']).rstrip()} {note}"

    def earlier_owner_text(self, turns: int = EARLIER_TURNS) -> str:
        """The owner's messages of the last `turns` kept turns that ended within
        `llm.target_window_s` (for the said-check's target words; see ChangeGuard)."""
        window = self.settings.profile.llm.target_window_s
        now = self.clock()
        with self._history_lock:  # one record per kept turn, filtered together (E4)
            users = [m for m in self._history if m["role"] == "user"]
            pairs = list(zip(users, self._turns, strict=True))
        recent = pairs[-turns:] if turns > 0 else []
        return " ".join(str(m["content"]) for m, t in recent if now - t.ended <= window)
