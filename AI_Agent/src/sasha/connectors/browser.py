"""The `browser` connector: the assistant's own browser, phase 1 (read-only).

Off by default; the owner ticks it per bot in the Bots screen. A bot with it can't also
have a private-data connector (`browser.policy`, review B4). The only setting is the
search engine template: DuckDuckGo's HTML results page by default, changeable.
"""

from __future__ import annotations

import itertools
import threading
from collections import OrderedDict
from typing import Any

from sasha.browser.chrome import find_chrome
from sasha.browser.origin import normalise
from sasha.browser.tools import BrowserBridge, BrowserConversation, browser_tools
from sasha.connectors.base import ConnectorError, SetupCheck, TestResult
from sasha.conversation import building_for_bot
from sasha.tools import Tool

TOOL_NAMES = [
    "search",
    "open_url",
    "read_page",
    "click",
    "scroll",
    "back",
    "list_tabs",
    "switch_tab",
]
_conversations = itertools.count(1)
MAX_HOLDERS = 256
# DuckDuckGo's plain HTML results page (no scripts needed), chosen by the owner.
DEFAULT_SEARCH = "https://html.duckduckgo.com/html/?q={q}"


def check_template(value: Any) -> str:
    """Empty (no search yet) or an https address with exactly one `{q}` in its query."""
    text = str(value or "").strip()
    if not text:
        return ""
    address = normalise(text.replace("{q}", "x"))
    if not text.startswith("https://") or text.count("{q}") != 1 or address is None:
        raise ConnectorError("search_template: an https:// address with one {q}, or empty")
    if "{q}" in text.split("?", 1)[0]:
        raise ConnectorError("search_template: {q} must be in the query part")
    return text


class BrowserConnector:
    id = "browser"
    kind = "browser"
    name = "Browser"

    def __init__(self, bridge: BrowserBridge) -> None:
        self.bridge = bridge
        self.holders: OrderedDict[str, str] = OrderedDict()  # browser holder -> its bot
        self.helper_holders: set[str] = set()  # holders of browse helpers (via "helper")
        self._holders_lock = threading.Lock()

    @property
    def secret_env(self) -> list[str]:
        return []  # no secrets: nothing to sign in to in phase 1

    def default_enabled(self) -> bool:
        return False

    def default_settings(self) -> dict[str, Any]:
        return {"search_template": DEFAULT_SEARCH}  # the owner's choice; changeable

    def validate_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        unknown = set(settings) - {"search_template"}
        if unknown:
            raise ConnectorError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
        return {"search_template": check_template(settings.get("search_template"))}

    def setup(self) -> SetupCheck:
        if find_chrome() is None:
            return SetupCheck(False, "Chrome or Chromium is not installed")
        return SetupCheck(True, "Uses its own private Chrome profile, read-only for now")

    def tool_names(self, settings: dict[str, Any]) -> list[str]:
        return list(TOOL_NAMES)

    def build_tools(self, settings: dict[str, Any]) -> list[Tool]:
        return browser_tools(self.conversation(settings))

    def conversation(self, settings: dict[str, Any], helper: bool = False) -> BrowserConversation:
        """One conversation's own browser holder (a raw-browser bot, or the browse
        helper of one main conversation, review P6)."""
        holder = f"conversation-{next(_conversations)}"
        bot = building_for_bot.get()
        if bot is not None:  # for the live view's holder_bot; the newest MAX_HOLDERS only
            with self._holders_lock:  # tools are built on several worker threads at once
                self.holders[holder] = bot
                if helper:
                    self.helper_holders.add(holder)
                while len(self.holders) > MAX_HOLDERS:
                    gone, _ = self.holders.popitem(last=False)
                    self.helper_holders.discard(gone)
        return BrowserConversation(
            self.bridge,
            holder=holder,
            search_template=settings.get("search_template") or None,
        )

    def test(self, settings: dict[str, Any]) -> TestResult:
        exe = find_chrome()
        if exe is None:
            return TestResult(False, "Chrome or Chromium is not installed")
        engine = "set" if settings.get("search_template") else "not set yet"
        return TestResult(True, f"Chrome found; search engine {engine}")
