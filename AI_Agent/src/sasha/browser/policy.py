"""Which connectors may sit next to the browser on one bot (review condition B4).

A bot with the `browser` connector may not also have a connector that reads private
data, so a page can never steer the model into leaking it: Home Assistant now; mail,
calendar and MCP servers when they exist. The server enforces this twice: when a bot is
saved, and again when its tools are built for a conversation.
"""

from __future__ import annotations

from collections.abc import Iterable

BROWSER = "browser"
PRIVATE_KINDS = frozenset({"home_assistant", "gmail", "calendar", "mcp"})


class BrowserCombinationError(ValueError):
    """A bot asked for the browser together with a private-data connector."""


def kind_of(connector_id: str) -> str:
    """Built-in connectors use their kind as id; instances look like `mcp:<name>`."""
    return connector_id.split(":", 1)[0]


def check_combination(connector_ids: Iterable[str]) -> None:
    ids = list(connector_ids)
    if BROWSER not in ids:
        return
    private = sorted({kind_of(c) for c in ids} & PRIVATE_KINDS)
    if private:
        raise BrowserCombinationError(
            "A bot with the browser can't also use "
            + ", ".join(private)
            + " (pages could steer it into reading private data). Use a separate bot."
        )
