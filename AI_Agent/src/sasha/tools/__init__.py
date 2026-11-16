"""Tools the LLM can call. Built from config + secrets; missing secrets disable a tool."""

from __future__ import annotations

from sasha.config import Settings
from sasha.tools.base import Tool, ToolError, ToolRegistry, ToolResult
from sasha.tools.home_assistant import HomeAssistantClient, build_home_assistant_tools

__all__ = ["Tool", "ToolError", "ToolRegistry", "ToolResult", "build_tools"]


def build_tools(settings: Settings) -> tuple[ToolRegistry, list[str]]:
    """Return the registry and human-readable warnings about disabled tools."""
    from sasha.tools.clock import clock_tool
    from sasha.tools.numbers import number_tools

    # read-only, always available
    registry = ToolRegistry([clock_tool(), *number_tools(settings.paths.config_dir)])
    warnings: list[str] = []
    ha = settings.tools.home_assistant
    if ha.enabled:
        url, token = settings.secrets.ha_url, settings.secrets.ha_token
        if url and token:
            client = HomeAssistantClient(url, token.get_secret_value(), ha)
            for tool in build_home_assistant_tools(client):
                registry.register(tool)
        else:
            warnings.append("Home Assistant tools disabled: set HA_URL and HA_TOKEN in .env")
    return registry, warnings
