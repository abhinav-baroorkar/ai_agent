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
        self.holding = any(c.holding for c in checks)
        self.held = ""

    def feed(self, piece: str) -> str:
        """Streamed model text in (worker thread); the part to send and speak now out."""
        if self.holding:
            self.held += piece
            return ""
        return piece

    def finish(self, text: str, tool_calls: list[dict[str, Any]]) -> tuple[str, str]:
        """(text still to send and speak, the final reply text)."""
        final = text
        for part in self.checks:
            final = part.finish(final, tool_calls)[1]
        if self.holding:
            return final, final
        return (final[len(text.rstrip()) :] if final != text else ""), final
