"""Tool definitions and a registry that the orchestrator hands to the LLM."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


class ToolError(Exception):
    """An expected failure the LLM should see as a message (bad input, not allowed...)."""


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
            return _error(str(exc))
        except Exception as exc:
            return _error(f"{type(exc).__name__}: {exc}")
        return ToolResult(ok=True, content=json.dumps(result, ensure_ascii=False, default=str))


def _error(message: str) -> ToolResult:
    return ToolResult(ok=False, content=json.dumps({"error": message}, ensure_ascii=False))
