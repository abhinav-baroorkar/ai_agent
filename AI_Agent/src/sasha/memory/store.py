"""The household's notes, in `Data/state/memory.json` (git-ignored; design §2).

One store per brain. A note made on a wake turn starts `pending` (M1): it reaches no
prompt until the owner keeps it on screen. At the cap new notes are refused, never the
oldest dropped (M6). Forgotten means gone: removed from the file, no trash. Telemetry
gets counts and labels only, never text. Every change is pushed to open sockets as one
`memory` frame (the whole list) through `notify`.
"""

from __future__ import annotations

import contextlib
import secrets
import threading
import time
import weakref
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ValidationError

from sasha.config_memory import MemoryConfig
from sasha.state import JsonStore
from sasha.telemetry import TelemetryWriter

SOURCES = ("chat", "window", "wake")


class Note(BaseModel):
    id: str
    text: str
    created_at: float
    source: str = "chat"  # chat | window | wake
    bot_id: str = "main"
    lang: str = "en"
    status: str = "active"  # active | pending (voice-made, waiting for the owner)
    say_aloud: bool = False  # M2: may a wake turn read it?

    def to_api(self) -> dict[str, Any]:
        return self.model_dump()


class MemoryError(ValueError):
    """A refused change; `reason` is a label (full, unknown...)."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class MemoryStore:
    def __init__(
        self,
        config: MemoryConfig,
        store: JsonStore,
        telemetry: TelemetryWriter | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.store = store
        self.telemetry = telemetry
        self.clock = clock
        self.lock = threading.RLock()
        self.notify: Callable[[dict[str, Any]], None] = lambda _frame: None  # set by the app
        self.notes: dict[str, Note] = {}
        # Every open conversation's own "drop my turns from the last N seconds", so a
        # forgotten window takes what was said in it too (weak: a closed one drops out).
        self._conversations: list[tuple[str, weakref.WeakMethod[Callable[[float], int]]]] = []
        for raw in store.load().get("notes") or []:
            try:
                note = Note.model_validate(raw)
            except ValidationError:
                continue  # a hand-edited broken entry: skipped, not fatal
            self.notes[note.id] = note

    # --- reading ---------------------------------------------------------------------
    def all(self) -> list[Note]:
        """Newest first."""
        with self.lock:
            return sorted(self.notes.values(), key=lambda n: n.created_at, reverse=True)

    def active(self) -> list[Note]:
        return [n for n in self.all() if n.status == "active"]

    def get(self, note_id: str) -> Note:
        with self.lock:
            note = self.notes.get(note_id)
        if note is None:
            raise MemoryError("No such note.", "unknown")
        return note

    def snapshot(self) -> dict[str, Any]:
        return {
            "notes": [n.to_api() for n in self.all()],
            "count": len(self.notes),
            "max": self.config.max_items,
        }

    # --- writing ---------------------------------------------------------------------
    def _save(self, event: str, count: int = 1) -> None:
        """Under the lock: write the file, then tell the sockets and telemetry."""
        self.store.save({"notes": [n.model_dump() for n in self.notes.values()]})
        self._event(event, count)
        with contextlib.suppress(Exception):  # a socket that went away never fails a change
            self.notify({"type": "memory", **self.snapshot()})

    def _event(self, event: str, count: int = 1) -> None:
        """Telemetry: a label and counts, never a note's text (the sentinel test)."""
        if self.telemetry is None or not self.telemetry.enabled:
            return
        started = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")
        record = {"schema": 1, "kind": "memory", "started_at": started,
                  "event": f"memory_{event}", "count": count, "items": len(self.notes)}  # fmt: skip
        with contextlib.suppress(OSError):  # telemetry must never break a note
            self.telemetry.write(record)

    def refused(self, reason: str) -> None:
        self._event(f"refused:{reason}", 1)

    def add(self, text: str, source: str, bot_id: str, lang: str = "en") -> Note:
        if source not in SOURCES:
            raise ValueError(f"unknown source {source!r}")
        with self.lock:
            if len(self.notes) >= self.config.max_items:
                raise MemoryError("The notes are full.", "full")  # M6: never evict
            note = Note(
                id=secrets.token_hex(4),
                text=text,
                created_at=self.clock(),
                source=source,
                bot_id=bot_id,
                lang=lang,
                status="pending" if source == "wake" else "active",
            )
            while note.id in self.notes:
                note.id = secrets.token_hex(4)
            self.notes[note.id] = note
            self._save("pending" if note.status == "pending" else "added")
            return note

    def keep(self, note_id: str) -> Note:
        """The owner keeps a voice-made note: active, and it may be said aloud (M1, M2).
        Only a waiting note: a stale click on a private note must not make it readable
        on wake turns (S2)."""
        with self.lock:
            note = self.get(note_id)
            if note.status != "pending":
                raise MemoryError("Only a waiting note can be kept.", "not_pending")
            note.status, note.say_aloud = "active", True
            self._save("kept")
            return note

    def set_say_aloud(self, note_id: str, value: bool) -> Note:
        with self.lock:
            note = self.get(note_id)
            if note.status != "active":
                raise MemoryError("Keep the note first.", "pending")
            note.say_aloud = value
            self._save("say_aloud")
            return note

    def delete(self, note_id: str, how: str = "id") -> Note:
        with self.lock:
            note = self.get(note_id)
            del self.notes[note.id]
            self._save(f"forgotten:{how}")
            return note

    def forget_since(self, since: float) -> list[Note]:
        """Every note made at or after `since`, all sources and states."""
        with self.lock:
            gone = [n for n in self.notes.values() if n.created_at >= since]
            for note in gone:
                del self.notes[note.id]
            if gone:
                self._save("forgotten:minutes", len(gone))
            return gone

    def add_conversation(self, place: str, forget_turns: Callable[[float], int]) -> None:
        """`place`: "home", "listener" or a bot id, for the forget report."""
        with self.lock:
            self._conversations = [(p, r) for p, r in self._conversations if r() is not None]
            self._conversations.append((place, weakref.WeakMethod(forget_turns)))

    def forget_window(self, seconds: float) -> dict[str, Any]:
        """ "Forget the last N minutes": the notes made in that window (waiting ones too)
        and every open conversation's turns from it (Home, the listener, every chat).
        Every socket gets `forgot`, so the pages drop their own turns of that window."""
        gone = self.forget_since(self.clock() - seconds)
        turns: dict[str, int] = {}
        with self.lock:
            conversations = list(self._conversations)
        for place, ref in conversations:
            forget_turns = ref()
            if forget_turns is not None and (count := forget_turns(seconds)):
                turns[place] = turns.get(place, 0) + count
        with contextlib.suppress(Exception):
            self.notify({"type": "forgot", "since_s": round(seconds)})
        items = [{"id": n.id, "text": n.text} for n in gone]
        return {"count": len(gone), "items": items, "turns": turns}

    def clear(self) -> list[Note]:
        """Forget everything: the file is rewritten empty, not hidden. Returns exactly
        the notes removed (read under the lock)."""
        with self.lock:
            gone = list(self.notes.values())
            self.notes.clear()
            self._save("forgotten:everything", len(gone))
            return gone
