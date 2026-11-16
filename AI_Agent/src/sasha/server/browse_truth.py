"""After a web lookup that brought nothing back, the server says so (found live,
2026-11-16: "Search the web for the latest stable Python version" got the model's own
old answer, "3.12 as of early 2025", with no word that the search was blocked).

When every `browse` of the turn failed, the reply is the server's own line for the
reason (`asks` of the call: a person check, nothing found, or anything else), never the
model's text. The model's own knowledge may follow only after the marker the tool told
it to use ("I couldn't check the web. From what I already know: ..."). Once a lookup
has failed, the rest of the reply is held (typed and spoken turns alike) so nothing
that reads like a web result is shown or spoken first. A turn with a lookup that
worked is left alone.
"""

from __future__ import annotations

from typing import Any

from sasha.browser.helper import BROWSE_TOOL
from sasha.browser.pages import PERSON_CHECK
from sasha.config_prompts import PromptLines


class BrowseTruth:
    def __init__(self, lines: PromptLines) -> None:
        self.lines = lines
        self.holding = False

    def on_tool(self, name: str, ok: bool) -> None:
        """Called on the worker thread as each tool returns, before any later text."""
        if name == BROWSE_TOOL and not ok:
            self.holding = True

    def line(self, reason: str) -> str:
        if reason == "person_check":
            return PERSON_CHECK
        if reason == "nothing_found":
            return self.lines.web_nothing_found
        return self.lines.web_failed

    def finish(self, text: str, tool_calls: list[dict[str, Any]]) -> tuple[str, str]:
        calls = [c for c in tool_calls if c.get("name") == BROWSE_TOOL]
        if not calls or any(c.get("ok") for c in calls):
            return "", text
        reasons = [str(c.get("asks") or "") for c in calls]
        reason = "person_check" if "person_check" in reasons else reasons[-1]
        line = self.line(reason)
        marker = self.lines.web_unchecked
        at = text.lower().find(marker.lower())
        final = f"{line} {text[at:].strip()}" if at >= 0 else line
        return final, final
