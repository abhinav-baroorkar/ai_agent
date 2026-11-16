"""One check per typed or window turn, made of the timers' and memory's own checks.

Each check may hold the model's text (`holding`) and, at the end of the turn, append
its line or replace the text. The chain holds when any part holds (a part that doesn't
hold only appends), runs the parts in order on the final text, and says what is still
to send and speak: everything when it held, else what the parts appended.
"""

from __future__ import annotations

from typing import Any, Protocol


class TurnCheck(Protocol):
    holding: bool

    def finish(self, text: str, tool_calls: list[dict[str, Any]]) -> tuple[str, str]: ...


class ChainCheck:
    def __init__(self, checks: list[TurnCheck]) -> None:
        self.checks = checks
        self.held = ""
        self.sent = ""  # what passed before a part started holding (it can't be unsent)
        self.replaced = False  # a part replaced the model's text (the turn's own truth)

    @property
    def holding(self) -> bool:
        """A part may start holding mid-turn (a failed web lookup, browse_truth.py)."""
        return any(c.holding for c in self.checks)

    def on_tool(self, name: str, ok: bool) -> None:
        """Each tool's result, on the worker thread, before any later text."""
        for part in self.checks:
            hook = getattr(part, "on_tool", None)
            if hook is not None:
                hook(name, ok)

    def feed(self, piece: str) -> str:
        """Streamed model text in (worker thread); the part to send and speak now out."""
        if self.holding:
            self.held += piece
            return ""
        self.sent += piece
        return piece

    def finish(self, text: str, tool_calls: list[dict[str, Any]]) -> tuple[str, str]:
        """(text still to send and speak, the final reply text)."""
        final = text
        for part in self.checks:
            final = part.finish(final, tool_calls)[1]
        self.replaced = not final.startswith(text.rstrip())
        if self.holding:  # all of it, but never again what already went out
            sent = self.sent.rstrip()
            rest = final[len(sent) :].lstrip() if sent and final.startswith(sent) else final
            return rest, final
        if self.replaced:  # text it didn't hold was already sent: never a slice (R2);
            return "", final  # the history keeps the replacement (the socket's shown text)
        return (final[len(text.rstrip()) :] if final != text else ""), final
