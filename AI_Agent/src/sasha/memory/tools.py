"""The memory tools (design §1, §4-§6): `remember`, `forget`, `list_memories`.

Every rule is the server's, checked against the owner's own messages
(`current_turn.user_messages`), never the model's text or a tool result:
- `remember` needs a remember phrase (M4), content of its own, every content word and
  number of the note said by the owner (M3), no secret (M7), room under the cap (M6).
  On a wake turn the note is `pending` until the owner keeps it on screen (M1).
- `forget` needs a forget phrase; minutes must have been said; "everything" must be said
  and confirmed by a yes in the next message. On a wake turn only voice-made notes can
  be forgotten; anything else is for the screen.
- `list_memories` on a wake turn gives counts and voice-made notes only (M2).
A refusal is a `MemoryAsk` whose reason picks the server's own line (memory/truth.py).
The source of a call comes from the turn itself (wake, window or chat).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sasha.config_memory import MemoryConfig
from sasha.conversation import TurnContext
from sasha.memory import check
from sasha.memory.store import MemoryError, MemoryStore, Note
from sasha.server.timers.said import tokens
from sasha.tools import Tool, ToolError, ToolRegistry

MEMORY_TOOLS = ("remember", "forget", "list_memories")
CHANGING = ("remember", "forget")
Turn = Callable[[], TurnContext | None]


class MemoryAsk(ToolError):
    """Refused or unclear: `asks` (= reason) picks the server's line."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = self.asks = reason


def _source(turn: TurnContext) -> str:
    return {"wake": "wake", "voice": "window"}.get(turn.input_mode, "chat")


class MemoryTools:
    def __init__(self, store: MemoryStore, said: Turn, bot_id: str, lang: str = "en") -> None:
        self.store, self.said, self.bot_id, self.lang = store, said, bot_id, lang
        self.cfg: MemoryConfig = store.config
        self.made: list[tuple[str, int, int]] = []  # (note id, user-message count, turn id)
        self.confirm_at: int | None = None  # user-message count when "everything" was asked
        self.wake_made: list[float] = []

    # --- helpers -------------------------------------------------------------------
    def _turn(self) -> TurnContext:
        turn = self.said()
        if turn is None or not turn.user_messages:
            raise MemoryAsk("Not in a conversation.", "not_asked")
        return turn

    def _owner_text(self, turn: TurnContext) -> str:
        """This turn's words, with the asked-back pair (check.owner_pair, which the
        turn's truth check reads too)."""
        pair = check.owner_pair(
            turn.user_messages, turn.previous_reply_asked, turn.since_previous_s, self.cfg
        )
        return "\n".join(pair)

    def _refuse(self, reason: str, message: str) -> MemoryAsk:
        self.store.refused(reason)
        return MemoryAsk(message, reason)

    # --- the tools -------------------------------------------------------------------
    def remember(self, text: Any = "") -> dict[str, Any]:
        turn = self._turn()
        owner = self._owner_text(turn)
        note = " ".join(str(text or "").split())[: self.cfg.max_text]
        if not check.asks_to_remember(owner, self.cfg):
            raise self._refuse("not_asked", "The owner didn't ask to remember anything.")
        if check.has_secret(owner, self.cfg) or check.has_secret(note, self.cfg):
            raise self._refuse("secret", "Secrets are never stored. Say so.")
        if not check.has_content(owner, self.cfg) or not check.content_words(note, self.cfg):
            raise self._refuse("say_what", "Nothing to remember was said. Ask what.")
        if check.unsaid_words(note, owner, self.cfg) or check.unsaid_numbers(note, owner):
            raise self._refuse("not_said", "Use only the owner's own words for the note.")
        this_turn = [m for m in self.made if m[2] == turn.turn_id]
        if len(this_turn) >= self.cfg.max_per_turn:
            raise self._refuse("too_many", "Too many notes at once.")
        source = _source(turn)
        if source == "wake":
            now = self.store.clock()
            self.wake_made = [t for t in self.wake_made if now - t < self.cfg.wake_window_s]
            if len(self.wake_made) >= self.cfg.wake_max_per_window:
                raise self._refuse("wake_limit", "No more notes by voice for now.")
            self.wake_made.append(now)
        try:
            saved = self.store.add(note, source, self.bot_id, self.lang)
        except MemoryError as exc:
            raise self._refuse(exc.reason, str(exc)) from None
        self.made.append((saved.id, len(turn.user_messages), turn.turn_id))
        if saved.status == "pending":  # M1: never in a prompt until the owner keeps it
            return {"saved": "for the owner to check on screen", "status": "pending"}
        # S3: the server shows what was really kept ("Noted: ...").
        return {"saved": saved.text, "status": "active", "report": {"noted": saved.text}}

    def _mine(self, turn: TurnContext) -> Note | None:
        """The note this conversation made last, if within the last turns."""
        if not self.made:
            return None
        note_id, count, _ = self.made[-1]
        if len(turn.user_messages) - count >= self.cfg.last_turns:
            return None
        return self.store.notes.get(note_id)

    def _by_words(self, which: str) -> list[Note]:
        wanted = [check.stem(w) for w in check.content_words(which, self.cfg)]
        found = []
        for note in self.store.all():
            have = {check.stem(w) for w in tokens(note.text)}
            if wanted and all(w in have for w in wanted):
                found.append(note)
        return found

    def forget(
        self, which: Any = "", minutes: Any = None, everything: Any = False
    ) -> dict[str, Any]:
        turn = self._turn()
        owner = turn.user_messages[-1]
        wake = _source(turn) == "wake"
        # R1: only a plain yes in the very next message, to the server's own question,
        # and only for the same everything=true call; anything else drops the question.
        asked_at, self.confirm_at = self.confirm_at, None
        confirming = (
            asked_at is not None
            and everything is True
            and len(turn.user_messages) == asked_at + 1
            and check.says_yes(owner, self.cfg)
        )
        if not confirming and not check.asks_to_forget(owner, self.cfg):
            raise self._refuse("not_asked", "The owner didn't ask to forget anything.")
        if everything is True:
            return self._everything(turn, owner, wake, confirming)
        if minutes not in (None, "", 0):
            return self._minutes(minutes, owner, wake)
        key = " ".join(str(which or "").split())
        if key.lower() in ("", "last", "that", "it", "this"):
            note = self._mine(turn)
            if note is None:
                raise self._refuse("which_note", "Which note? Ask (list_memories).")
            notes = [note]
        else:
            # R6: the note is named by the owner's own words this turn, never by the
            # model alone (an id or a word the owner didn't say is asked back).
            if not check.content_words(key, self.cfg) or check.unsaid_words(key, owner, self.cfg):
                raise self._refuse("which_note", "Name the note with the owner's own words.")
            notes = self._by_words(key)
            if len(notes) != 1:
                raise self._refuse("which_note", "Not exactly one note matches. Ask which.")
        if wake and any(n.source != "wake" for n in notes):
            raise self._refuse("on_screen", "Only voice-made notes can be forgotten by voice.")
        for note in notes:
            self.store.delete(note.id, "words" if key else "last")
        return self._forgot(notes, wake)

    def _minutes(self, minutes: Any, owner: str, wake: bool) -> dict[str, Any]:
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            raise self._refuse("bad_minutes", "minutes must be a whole number.")
        value = int(minutes)
        if value != minutes or not 1 <= value <= self.cfg.max_forget_minutes:
            raise self._refuse("bad_minutes", "minutes must be a whole number of minutes.")
        if value in check.unsaid_numbers(str(value), owner):
            raise self._refuse("bad_minutes", "The owner didn't say that number. Ask.")
        if wake:
            raise self._refuse("on_screen", "Forgetting a time window is for the screen.")
        report = self.store.forget_window(value * 60.0)  # every conversation's turns too
        turns = sum(report["turns"].values())
        if not report["count"] and not turns:
            raise self._refuse("no_notes", "Nothing was said or noted in that window.")
        # W1: counts only, so a forgotten note's text can't come back through the reply.
        counts = {"notes": report["count"], "turns": turns}
        return {"forgot": counts, "report": counts}

    def _everything(
        self, turn: TurnContext, owner: str, wake: bool, confirming: bool
    ) -> dict[str, Any]:
        if wake:
            raise self._refuse("on_screen", "Forgetting everything is for the screen.")
        if not confirming:
            if not check.says_everything(owner, self.cfg):
                raise self._refuse("which_note", "The owner didn't say everything. Ask which.")
            if not self.store.notes:
                raise self._refuse("no_notes", "There are no notes.")
            self.confirm_at = len(turn.user_messages)
            raise self._refuse("confirm_everything", "Ask the owner to confirm with a yes.")
        count = len(self.store.clear())
        return {"forgot": {"notes": count}, "report": {"notes": count}}

    def _forgot(self, notes: list[Note], wake: bool) -> dict[str, Any]:
        if not notes:
            raise self._refuse("no_notes", "No notes are in that window.")
        counts = {"notes": len(notes)}  # W1, R4: counts only, never the text
        return {"forgot": counts, "report": counts}

    def list_memories(self) -> dict[str, Any]:
        """Active notes only; a waiting (voice-made, not yet kept) note is only counted,
        never shown, so its text can't reach a prompt through this result (R4, M1). On
        a wake turn only notes marked "ok to say aloud" (M2)."""
        turn = self.said()
        notes = self.store.all()
        active = [n for n in notes if n.status == "active"]
        pending = sum(n.status == "pending" for n in notes)
        if turn is not None and _source(turn) == "wake":  # M2: what the room may hear
            aloud = [n.text for n in active if n.say_aloud][:10]
            return {"say_aloud": aloud, "others": len(active) - len(aloud), "waiting": pending}
        return {"notes": [{"text": n.text, "say_aloud": n.say_aloud} for n in active],
                "waiting": pending}  # fmt: skip

    def tools(self) -> list[Tool]:
        obj = {"type": "object", "properties": {}}
        return [
            Tool("remember", "Keep a note the owner asked you to remember, in the owner's "
                 "own words as said, first person ('I take the 8:10 bus'). Only when asked.",
                 {"type": "object", "properties": {"text": {"type": "string"}},
                  "required": ["text"]}, self.remember),
            Tool("forget", "Forget notes when the owner asks: which='last' (the one just "
                 "made), a note id or its words; or minutes (the last N minutes); or "
                 "everything=true (needs the owner's yes).",
                 {"type": "object", "properties": {
                     "which": {"type": "string"}, "minutes": {"type": "integer"},
                     "everything": {"type": "boolean"}}}, self.forget),
            Tool("list_memories", "The notes the owner asked you to remember.", obj,
                 self.list_memories),
        ]  # fmt: skip


def add_memory_tools(
    registry: ToolRegistry, store: MemoryStore, said: Turn, bot_id: str
) -> tuple[ToolRegistry, MemoryTools]:
    """`registry` with the memory tools (any earlier ones replaced), and their owner."""
    out = ToolRegistry(registry.get(n) for n in registry.names if n not in MEMORY_TOOLS)
    memory = MemoryTools(store, said, bot_id)
    for tool in memory.tools():
        out.register(tool)
    return out, memory
