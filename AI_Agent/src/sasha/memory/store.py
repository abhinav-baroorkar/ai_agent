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
        """The owner keeps a voice-made note: active, and it may be said aloud (M1, M2)."""
        with self.lock:
            note = self.get(note_id)
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

    def clear(self) -> int:
        """Forget everything: the file is rewritten empty, not hidden."""
        with self.lock:
            count = len(self.notes)
            self.notes.clear()
            self._save("forgotten:everything", count)
            return count
