"""Connectors give bots tools. v0 kind: `home_assistant`. Next: `mcp`."""

from __future__ import annotations

from collections.abc import Callable

from sasha.config import Secrets, Settings
from sasha.connectors.base import Connector, ConnectorError, SetupCheck, Status, TestResult
from sasha.connectors.home_assistant import HomeAssistantConnector
from sasha.connectors.registry import ConnectorRegistry
from sasha.state import JsonStore

__all__ = [
    "Connector",
    "ConnectorError",
    "ConnectorRegistry",
    "HomeAssistantConnector",
    "SetupCheck",
    "Status",
    "TestResult",
    "build_connectors",
]


def build_connectors(settings: Settings, secrets: Callable[[], Secrets]) -> ConnectorRegistry:
    connectors: list[Connector] = [HomeAssistantConnector(settings.tools.home_assistant, secrets)]
    store = JsonStore(settings.paths.state_dir / "connectors.json")
    return ConnectorRegistry(connectors, store)
