"""The prompt's memory section (design §3; the review's M2, M5).

A fixed heading, then the active notes, newest first, as one data block wrapped the
way tool results are (`untrusted_data`), within `memory.prompt_chars`. Pending notes
(voice-made, not yet kept) never appear (M1). On a wake turn only notes marked "ok to
say aloud" appear (M2); with none, there is no section. Rebuilt for every turn, so a
note kept or forgotten counts from the very next turn, on every socket.
"""

from __future__ import annotations

import json

from sasha.config_prompts import PromptLines
from sasha.conversation import current_turn
from sasha.memory.store import MemoryStore, Note
from sasha.orchestrator import wrap_untrusted


def notes_for_turn(store: MemoryStore, wake: bool) -> list[Note]:
    notes = store.active()
    return [n for n in notes if n.say_aloud] if wake else notes


def memory_section(store: MemoryStore, lines: PromptLines) -> str:
    turn = current_turn.get()
    notes = notes_for_turn(store, wake=turn is not None and turn.input_mode == "wake")
    kept: list[str] = []
    used = 0
    for note in notes:  # newest first, within the budget
        used += len(note.text) + 4
        if used > store.config.prompt_chars:
            break
        kept.append(note.text)
    if not kept:
        return ""
    return f"{lines.memory_head}\n{wrap_untrusted('memory', json.dumps(kept, ensure_ascii=False))}"
