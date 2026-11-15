"""The request backstop in log-only mode (review B8).

Phase 1 doesn't act on pages, but it counts what acting would trigger, so phase 2 can
decide on data whether to hold non-GET requests after unapproved clicks: every
request's method (from CDP `Network.requestWillBeSent`) is counted, and non-GET/HEAD
requests within `WINDOW_S` after a click are counted separately. Counts only: no URLs,
hosts, bodies or headers are kept (review B5).
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

WINDOW_S = 3.0
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class BackstopCounts:
    requests: Counter[str] = field(default_factory=Counter)  # by method
    clicks: int = 0
    unsafe_after_click: int = 0  # non-GET/HEAD within WINDOW_S after a click
    unsafe_without_click: int = 0  # page scripts on their own (analytics, beacons)


class LogOnlyBackstop:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.counts = BackstopCounts()
        self._last_click: float | None = None

    def click(self) -> None:
        self.counts.clicks += 1
        self._last_click = self.clock()

    def request(self, method: str) -> None:
        method = method.upper() if method.isalpha() and len(method) <= 10 else "OTHER"
        self.counts.requests[method] += 1
        if method in SAFE_METHODS:
            return
        recent = self._last_click is not None and self.clock() - self._last_click <= WINDOW_S
        if recent:
            self.counts.unsafe_after_click += 1
        else:
            self.counts.unsafe_without_click += 1

    def on_request_will_be_sent(self, params: dict[str, Any]) -> None:
        """A CDP `Network.requestWillBeSent` listener (reads only the method)."""
        request = params.get("request") or {}
        self.request(str(request.get("method", "OTHER")))
