"""Typed and window turns: the server, not the model, says what memory really did.

Like the timers' truth check (server/timers/truth.py), structurally (T1, M4). It reads
the same owner words as the tools, the asked-back pair included (R2):
- a refused secret (M7): the reply becomes the server's line on every path, the wake
  path too (R3), and both messages of the pair stay in the history with the secret
  and every number masked. A remember request with a secret or any number is held on
  typed turns too, so a reply repeating it is never shown;
- a forget that worked: the reply is the server's line from the tool's counts ("Forgot
  1 note and 2 earlier turns."), so a forgotten note's text can't come back (W1);
  forget requests are always held;
- "forget everything" asked to confirm: the server's question is always shown (R1);
- a tool's refusal (say what, not your words, full...): the server's line for it;
- a note that was kept: the server shows it back ("Noted: ...", S3);
- a remember (or forget) request with no `remember` (`forget`) that succeeded, and a
  reply that isn't all questions: "I didn't save that." ("Nothing was forgotten.").
On a spoken window turn for such a request the model's text is held and replaced.
On wake turns (`wake=True`) only the secret rule runs; the wake truthful reply (wake
truth.py) does the rest.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sasha.config_memory import MemoryConfig
from sasha.memory import check
from sasha.server.timers.truth import all_questions

Redact = Callable[[Callable[[str], str], int], None]


class MemoryTruth:
    def __init__(
        self, cfg: MemoryConfig, pair: list[str], spoken: bool, redact: Redact, wake: bool = False
    ) -> None:
        self.cfg, self.pair, self.redact, self.wake = cfg, pair, redact, wake
        owner = "\n".join(pair)
        self.remembering = check.asks_to_remember(owner, cfg)
        self.forgetting = not self.remembering and check.asks_to_forget(pair[-1], cfg)
        self.secret = self.remembering and check.has_secret(owner, cfg)
        risky = self.remembering and (self.secret or check.has_number(owner))
        if wake:
            self.holding = risky
        else:
            asking = self.remembering or self.forgetting
            self.holding = risky or self.forgetting or (spoken and asking)
        self.spoken = spoken
        self.held = ""

    def feed(self, piece: str) -> str:
        if self.holding:
            self.held += piece
            return ""
        return piece

    def _forgot(self, report: dict[str, Any]) -> str:
        parts = []
        for key in ("notes", "turns"):
            n = report.get(key)
            if isinstance(n, int) and n:
                one, many = self.cfg.units[key]
                parts.append(f"{n} {one if n == 1 else many}")
        what = self.cfg.lines["and"].join(parts) or f"0 {self.cfg.units['notes'][1]}"
        return self.cfg.lines["forgot"].format(what=what)

    def _secret(self) -> tuple[str, str]:
        self.redact(lambda said: check.mask_all(said, self.cfg), len(self.pair))  # M7
        line = self.cfg.lines["secret"]
        return line, line

    def finish(self, text: str, tool_calls: list[dict[str, Any]]) -> tuple[str, str]:
        mine = [c for c in tool_calls if c.get("name") in ("remember", "forget")]
        asks = [str(c["asks"]) for c in mine if not c.get("ok") and c.get("asks")]
        done = {str(c.get("name")): c for c in mine if c.get("ok")}
        lines = self.cfg.lines
        if self.secret or "secret" in asks:
            return self._secret()
        if self.wake:
            return self.held, text
        if "forget" in done:  # W1: the server's line from the counts, never the text
            line = self._forgot(dict(done["forget"].get("report") or {}))
            return line, line
        if asks and asks[-1] == "confirm_everything":  # R1: always the server's question
            line = lines["confirm_everything"]
        elif asks and asks[-1] in lines and asks[-1] != "not_asked":
            line = lines[asks[-1]]
            if all_questions(text) and not self.holding:
                return self.held, text  # the model asked it already
        elif "remember" in done:
            return self._noted(text, dict(done["remember"].get("report") or {}))
        elif self.remembering and not all_questions(text):
            line = lines["not_saved"]
        elif self.forgetting and not all_questions(text):
            line = lines["not_forgotten"]
        else:
            return self.held, text
        if self.holding:  # nothing was shown or spoken yet: the server's line instead
            return line, line
        return f" {line}", f"{text.rstrip()} {line}"

    def _noted(self, text: str, report: dict[str, Any]) -> tuple[str, str]:
        """S3: what was really kept, shown back after the reply (spoken: short only)."""
        kept = str(report.get("noted") or "").rstrip(".")
        if not kept:
            return self.held, text
        line = self.cfg.lines["noted"].format(text=kept)
        if self.spoken and len(kept) > self.cfg.spoken_noted_max:
            line = self.cfg.lines["noted_short"]
        if self.holding:
            said = f"{self.held.rstrip()} {line}".strip()
            return said, f"{text.rstrip()} {line}".strip()
        return f" {line}", f"{text.rstrip()} {line}"
