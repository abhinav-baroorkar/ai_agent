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

A bot with the browser itself (the direct tools) gets the same for a person check:
when its search met one and no browser step worked, the reply is the server's line,
which says where Take over is for that bot (found live, 2026-11-16: the model reworded
the tool's line and sent the owner to the wrong chat).

A Wikipedia lookup (browser/wikipedia.py) counts as a lookup too: when every lookup of
the turn failed and they were all Wikipedia's, the line says Wikipedia, not the web.
"""

from __future__ import annotations

import re
from typing import Any

from sasha.browser.helper import BROWSE_TOOL
from sasha.browser.pages import PERSON_CHECK, PERSON_CHECK_HERE
from sasha.browser.tools import DIRECT_TOOLS
from sasha.browser.wikipedia import WIKI_TOOL
from sasha.config_prompts import PromptLines

LOOKUP_TOOLS = (BROWSE_TOOL, WIKI_TOOL)


def asks_for_lookup(command: str, lines: PromptLines) -> bool:
    """The owner's message asks for a web lookup ("search", "look up", "online"...)."""
    words = re.findall(r"[\w']+", command.lower())
    text = " " + " ".join(words) + " "
    return any(f" {phrase.lower()} " in text for phrase in lines.lookup_words)


class BrowseTruth:
    def __init__(self, lines: PromptLines, command: str = "") -> None:
        self.lines = lines
        # Review BT2: on a lookup request, hold from the start, so an answer the model
        # writes before it looks ("The latest is 3.12. Let me confirm...") is never sent
        # first; released whole at the end when a lookup worked.
        self.holding = asks_for_lookup(command, lines)

    def on_tool(self, name: str, ok: bool) -> None:
        """Called on the worker thread as each tool returns, before any later text."""
        if name in (*LOOKUP_TOOLS, "search") and not ok:
            self.holding = True

    def line(self, reason: str, here: bool = False, wiki: bool = False) -> str:
        """`here`: the bot has the browser itself, so Take over is beside its chat;
        `wiki`: every failed lookup was Wikipedia's."""
        if reason == "person_check":
            return PERSON_CHECK_HERE if here else PERSON_CHECK
        if reason == "nothing_found":
            return self.lines.wiki_nothing_found if wiki else self.lines.web_nothing_found
        if reason == "not_set_up" and wiki:  # nothing was sent: the setting is empty
            return self.lines.wiki_not_set_up
        return self.lines.wiki_failed if wiki else self.lines.web_failed

    def finish(self, text: str, tool_calls: list[dict[str, Any]]) -> tuple[str, str]:
        final = self.finish_chain(text, text, tool_calls)
        return final, final

    def finish_chain(self, model: str, final: str, tool_calls: list[dict[str, Any]]) -> str:
        """Review BT1: replace only the model's own words; the lines the other checks
        added (the timers' "Nothing was set.", memory's secret line) stay, after it.
        `model`: the model's text; `final`: what the checks before made of it."""
        lookups = [c for c in tool_calls if c.get("name") in LOOKUP_TOOLS]
        direct = [c for c in tool_calls if c.get("name") in DIRECT_TOOLS]
        # the direct tools: only a person check is said by the server
        here = any(c.get("asks") == "person_check" for c in direct)
        calls = lookups + (direct if here else [])
        if not calls or any(c.get("ok") for c in lookups + direct):
            return final  # nothing to say, or something worked
        reasons = [str(c.get("asks") or "") for c in calls]
        reason = "person_check" if "person_check" in reasons else reasons[-1]
        wiki = all(c.get("name") == WIKI_TOOL for c in calls)
        line = self.line(reason, here, wiki)
        own = model.rstrip()
        if not final.startswith(own):  # a check replaced the model's text (a spoken
            return f"{line} {final}".strip()  # timer line, a secret): never bring it back
        added = final[len(own) :].strip()
        marker = self.lines.web_unchecked
        at = model.lower().find(marker.lower())
        marked = model[at:].strip() if at >= 0 else ""
        return " ".join(part for part in (line, marked, added) if part)
