"""Which connectors may sit next to the browser on one bot (review condition B4).

A bot with a `browser` connector may not also have a connector that could read private
data, so a page can never steer the model into leaking it. This is an allowlist on the
connector's real kind (not its id): next to the browser only kinds in `SAFE_KINDS` are
allowed, none today, so a browser bot has the browser and the built-in tools only. A new
kind, or a connector whose kind isn't known, is refused until someone decides otherwise.
The server enforces this twice: when a bot is saved, and again when its tools are built
for a conversation.
"""

from __future__ import annotations

from collections.abc import Mapping

BROWSER = "browser"
SAFE_KINDS: frozenset[str] = frozenset()  # kinds allowed next to the browser


class BrowserCombinationError(ValueError):
    """A bot asked for the browser together with a connector not marked safe for it."""


def _is_browser(connector_id: str, kind: str | None) -> bool:
    # an id that looks like a browser counts as one even if its kind isn't known
    return kind == BROWSER or connector_id.split(":", 1)[0] == BROWSER


def check_combination(kinds: Mapping[str, str | None]) -> None:
    """`kinds` maps each of the bot's connector ids to its kind (None if unknown)."""
    if not any(_is_browser(cid, kind) for cid, kind in kinds.items()):
        return
    others = sorted(
        cid
        for cid, kind in kinds.items()
        if not _is_browser(cid, kind) and (kind is None or kind not in SAFE_KINDS)
    )
    if others:
        raise BrowserCombinationError(
            "A bot with the browser can't also use "
            + ", ".join(others)
            + " (pages could steer it into reading private data). Use a separate bot."
        )
