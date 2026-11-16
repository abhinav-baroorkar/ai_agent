"""Tool definitions and a registry that the orchestrator hands to the LLM."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


class ToolError(Exception):
    """An expected failure the LLM should see as a message (bad input, not allowed...).

    `asks` is a reason label (never text) when the tool asks the person back, such as
    the timers' "ambiguous_period": the server can then ask its own fixed question."""

    asks: str = ""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object
    handler: Callable[..., Any]

    def spec(self) -> dict[str, Any]:
        """Tool spec in the OpenAI/Ollama function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str  # JSON text passed back to the LLM
    asks: str = ""  # the tool asked the person back: a reason label (ToolError.asks)
    # A tool's own short summary for the server (its result's `report`: numbers, or the
    # text it kept), so the server can say what happened instead of the model.
    report: dict[str, int | str] | None = None


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return _error(f"Unknown tool '{name}'. Available: {', '.join(self._tools)}")
        allowed = set(tool.parameters.get("properties", {}))
        required = set(tool.parameters.get("required", []))
        missing = required - arguments.keys()
        if missing:
            return _error(f"Missing required argument(s): {', '.join(sorted(missing))}")
        # Small models sometimes invent extra arguments; drop them rather than fail.
        clean = {k: v for k, v in arguments.items() if k in allowed}
        try:
            result = tool.handler(**clean)
        except ToolError as exc:
            return _error(str(exc), exc.asks)
        except Exception as exc:
            return _error(f"{type(exc).__name__}: {exc}")
        content = json.dumps(result, ensure_ascii=False, default=str)
        return ToolResult(ok=True, content=content, report=_report(result))


def _report(result: Any) -> dict[str, int | str] | None:
    raw = result.get("report") if isinstance(result, dict) else None
    if not isinstance(raw, dict):
        return None
    return {
        str(k): v
        for k, v in raw.items()
        if (isinstance(v, int) and not isinstance(v, bool))
        or (isinstance(v, str) and len(v) <= 300)
    }


def _error(message: str, asks: str = "") -> ToolResult:
    content = json.dumps({"error": message}, ensure_ascii=False)
    return ToolResult(ok=False, content=content, asks=asks)
