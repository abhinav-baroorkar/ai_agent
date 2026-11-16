"""The types one request passes around: its hooks, its reply, its failure, and the
wrapper that marks tool output as data (split out of orchestrator.py)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnHooks:
    """Optional callbacks for one request (the server uses them; the CLI doesn't).

    `on_delta` gets answer text as it streams, `on_tool(name, ok)` fires after each tool,
    and `should_stop()` returning true cancels the request (raises `LLMCancelled`).
    """

    on_delta: Callable[[str], None] | None = None
    on_tool: Callable[[str, bool], None] | None = None
    should_stop: Callable[[], bool] | None = None


@dataclass
class Reply:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    # Sites the turn's web results came from: the server's own `site`/`sites` fields of
    # tainting tools (browse: the tabs' record, P4), never the model's words.
    sites: list[str] = field(default_factory=list)


class RequestFailed(Exception):
    """A request failed. `tool_calls` lists tools that already ran (they may have acted).

    The original exception is the `__cause__`. Ctrl-C is never wrapped: KeyboardInterrupt
    propagates unchanged; `Assistant.last_tool_calls` then says what already ran.
    """

    def __init__(self, tool_calls: list[dict[str, Any]]) -> None:
        super().__init__("request failed")
        self.tool_calls = tool_calls


def wrap_untrusted(source: str, content: str) -> str:
    """Label tool output as data so the model never treats it as instructions."""
    try:
        data: Any = json.loads(content)
    except ValueError:
        data = content
    return json.dumps({"source": source, "untrusted_data": data}, ensure_ascii=False)
