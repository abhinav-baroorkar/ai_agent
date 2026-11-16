"""Typed and window turns: the server, not the model, says whether a note was kept.

Like the timers' truth check (server/timers/truth.py), structurally (T1, M4):
- a refused secret (M7): the reply becomes the server's line, on every path, and the
  owner's message stays in the history with the secret masked; the text is held on
  typed turns too, so a reply repeating the secret is never shown;
- a tool's refusal (say what, not your words, full...): the server's line for it;
- a remember (or forget) request with no `remember` (`forget`) that succeeded, and a
  reply that isn't all questions: "I didn't save that." ("Nothing was forgotten.").
On a spoken window turn for such a request the model's text is held and replaced.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sasha.config_memory import MemoryConfig
from sasha.memory import check
from sasha.server.timers.truth import all_questions

Redact = Callable[[Callable[[str], str]], None]


class MemoryTruth:
    def __init__(self, cfg: MemoryConfig, command: str, spoken: bool, redact: Redact) -> None:
        self.cfg, self.redact = cfg, redact
        self.remembering = check.asks_to_remember(command, cfg)
        self.forgetting = check.asks_to_forget(command, cfg)
        self.secret = self.remembering and check.has_secret(command, cfg)
        self.holding = self.secret or (spoken and (self.remembering or self.forgetting))
        self.held = ""

    def feed(self, piece: str) -> str:
        if self.holding:
            self.held += piece
            return ""
        return piece

    def finish(self, text: str, tool_calls: list[dict[str, Any]]) -> tuple[str, str]:
        ok = {c.get("name") for c in tool_calls if c.get("ok")}
        mine = [c for c in tool_calls if c.get("name") in ("remember", "forget")]
        asks = [str(c["asks"]) for c in mine if not c.get("ok") and c.get("asks")]
        lines = self.cfg.lines
        if self.secret or "secret" in asks:
            self.redact(lambda said: check.mask(said, self.cfg))  # M7: out of the history
            return lines["secret"], lines["secret"]
        if asks and asks[-1] in lines and asks[-1] != "not_asked":
            line = lines[asks[-1]]
            if all_questions(text) and not self.holding:
                return self.held, text  # the model asked it already
        elif self.remembering and "remember" not in ok and not all_questions(text):
            line = lines["not_saved"]
        elif self.forgetting and "forget" not in ok and not all_questions(text):
            line = lines["not_forgotten"]
        else:
            return self.held, text
        if self.holding:  # nothing was shown or spoken yet: the server's line instead
            return line, line
        return f" {line}", f"{text.rstrip()} {line}"
