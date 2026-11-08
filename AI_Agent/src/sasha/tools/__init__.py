"""Tools the LLM can call. Built from config + secrets; missing secrets disable a tool."""

from __future__ import annotations

from sasha.config import Settings
from sasha.tools.base import Tool, ToolError, ToolRegistry, ToolResult
from sasha.tools.home_assistant import HomeAssistantClient, build_home_assistant_tools

__all__ = ["Tool", "ToolError", "ToolRegistry", "ToolResult", "build_tools"]


def build_tools(settings: Settings) -> tuple[ToolRegistry, list[str]]:
    """Return the registry and human-readable warnings about disabled tools."""
    registry = ToolRegistry()
    warnings: list[str] = []
    ha = settings.tools.home_assistant
    if ha.enabled:
        url, token = settings.secrets.ha_url, settings.secrets.ha_token
        if url and token:
            for tool in build_home_assistant_tools(HomeAssistantClient(url, token, ha)):
                registry.register(tool)
        else:
            warnings.append("Home Assistant tools disabled: set HA_URL and HA_TOKEN in .env")
    return registry, warnings
