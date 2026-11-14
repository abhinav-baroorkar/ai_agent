"""What a connector is: something that gives a bot tools (Home Assistant, later MCP servers).

A connector knows its secrets only by env var name (`secret_env`); values are read
from the environment / `.env` at use time and never stored, returned or logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sasha.tools import Tool

Status = Literal["ok", "off", "needs_setup", "error"]


class ConnectorError(ValueError):
    """Bad input from the API (unknown connector, invalid settings). Shown to the user."""


@dataclass(frozen=True)
class SetupCheck:
    ready: bool  # all secrets present
    detail: str  # plain sentence for the settings screen


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # not a pytest test class

    ok: bool
    message: str


class Connector(Protocol):
    """One connector instance. `kind` says which implementation; `id` is unique.

    Built-in kinds have a fixed id (`home_assistant`). Future kinds such as `mcp` can
    have many instances (`mcp:<name>`), each with its own settings; the API is the same.
    """

    @property
    def id(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def secret_env(self) -> list[str]: ...

    def default_enabled(self) -> bool: ...

    def default_settings(self) -> dict[str, Any]: ...

    def validate_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Return the cleaned full settings, or raise `ConnectorError`."""
        ...

    def setup(self) -> SetupCheck: ...

    def tool_names(self, settings: dict[str, Any]) -> list[str]: ...

    def build_tools(self, settings: dict[str, Any]) -> list[Tool]:
        """Tools for one conversation. Only called when enabled and set up."""
        ...

    def test(self, settings: dict[str, Any]) -> TestResult:
        """Connectivity check. Must not raise and must not put secrets in the message."""
        ...
