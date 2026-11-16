"""Which changing tool calls the owner really asked for (P1/P3, memory M1; split out of
orchestrator.py). The check itself is sasha/said.py; this decides when it applies."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sasha.history import HistoryMixin
from sasha.said import DEVICE_TOOL, said_by_owner
from sasha.tools import ToolRegistry
from sasha.tools.base import ToolResult

# Review P1: while outside text (a `browse` result) is in the conversation, a changing
# tool must be named by the owner's current message. (tool, arguments, owner's message,
# his earlier recent messages) -> did he say it? Built per bot (sasha/said.py); without
# one, every such call is refused. `earlier` is built here, never by the model: the
# owner's messages of the last 2 kept turns that ended within `llm.target_window_s`
# (a target like "the bedroom light" may come from there; the action never does).
ChangeGuard = Callable[[str, dict[str, Any], str, str], bool]
SAME_TURN = "Changes can't follow a web lookup in the same request; ask the owner to say it again"
NAME_IT = "Say which device and what to do, and I'll do it."
SAY_IT = "Say in your own words what to change, and I'll do it."  # not a device (G4)


class GuardMixin(HistoryMixin):
    """The Assistant's guard side: needs `tools`, `change_guard` and `always_guard`."""

    tools: ToolRegistry
    change_guard: ChangeGuard | None
    always_guard: bool

    def _taints(self, name: str) -> bool:
        return name in self.tools and self.tools.get(name).taints

    def _change_refused(self, name: str, arguments: dict[str, Any], text: str) -> ToolResult | None:
        """P1/P3 (and M1 with `always_guard`): a tool not marked read-only, while outside
        text is in the context, must be said by the owner."""
        if name not in self.tools:
            return None
        tool = self.tools.get(name)
        if tool.read_only:
            return None
        if self._turn_browsed:  # the stricter first layer: never in the same turn
            message = SAME_TURN
        elif not (self.tainted or self.always_guard):
            return None
        elif tool.said_checked and not self.tainted:
            return None  # its own said-check covers memory's M1
        elif tool.said_checked:
            # Under a web-result taint (P1, review E3) its own check covers times, not
            # the free text a page could choose: every word of its `said_fields` must
            # also be the owner's, this turn or the previous one within the window.
            own = f"{text} {self.earlier_owner_text(turns=1)}"
            fields = (str(arguments.get(f) or "") for f in tool.said_fields)
            # S2: a field keeps exactly the negations of his current message
            if all(said_by_owner(field, own, negation_text=text) for field in fields):
                return None
            message = SAY_IT
        else:
            guard = self.change_guard
            earlier = "" if self.tainted else self.earlier_owner_text()  # P1 stays strict
            if guard is not None and guard(name, arguments, text, earlier):
                return None
            message = NAME_IT if name == DEVICE_TOOL else SAY_IT
        return ToolResult(ok=False, content=json.dumps({"error": message}, ensure_ascii=False))
