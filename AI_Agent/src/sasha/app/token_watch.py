"""The app window after a brain restart (found on the live machine, 2026-11-16).

Each brain launch writes a new window token; the page holds the one it was opened
with, so after a service restart, a crash or a reboot in the wrong order every browser
route answered 401/403 until the window was reopened. The shell now re-reads the token
file every few seconds; when it holds a new token (a new brain), the page is reloaded
with it. An outage (no file) changes nothing until the new brain has written its own.
Pure Python: the shell's Qt timer calls `check`.
"""

from __future__ import annotations

from collections.abc import Callable

WATCH_MS = 2000


class TokenWatch:
    def __init__(self, read: Callable[[], str | None], current: str | None = None) -> None:
        self.read = read
        self.current = current

    def take(self) -> str | None:
        """Read the token now and remember it (the page is being loaded with it)."""
        self.current = self.read()
        return self.current

    def check(self) -> str | None:
        """A new token if the brain has changed since the page was loaded, else None."""
        token = self.read()
        if token is None or token == self.current:
            return None
        self.current = token
        return token
