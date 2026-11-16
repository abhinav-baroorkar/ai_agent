"""The window comes up when the wake phrase is heard (app-shell design 6).

On an accepted wake the listener asks the app shell to come up (`launcher.open_shell`,
as its own user unit), never while the screen is locked or its state is unknown (the
guards are checked again at this moment), and at most as often as the profile's
`window` settings allow:
- one open or raise per `min_interval_s`, at most `per_hour`;
- a false wake is the phrase alone followed by no command, or a window closed within
  `closed_quickly_s` of being opened by a wake; `false_wakes_for_pause` of them within
  `false_wake_window_s` pause raising for `pause_s`.
Times only, in memory; nothing about what was said.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from sasha.config_app import WindowConfig

# -> "raised" | "started" | "browser" (the shell failed: Chrome) | "no_display" | ...
Opener = Callable[[], str]


class WindowOpener:
    def __init__(
        self,
        config: WindowConfig,
        allowed: Callable[[], bool],
        open_window: Opener,
        is_open: Callable[[], bool],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.allowed = allowed
        self.open_window = open_window
        self.is_open = is_open
        self.clock = clock
        self.opens: deque[float] = deque()
        self.false_wakes: deque[float] = deque()
        self.paused_until = 0.0
        self.waiting_since: float | None = None  # opened on the phrase alone, no command yet
        self.opened_at: float | None = None
        # Review W1: opening can take up to 10 s (the start check), and the command often
        # comes 0.3 s after the phrase: the open is marked under the lock first.
        self._lock = threading.Lock()

    def on_wake(self, command: bool) -> str:
        """What happened to the window: raised, started, or why not."""
        with self._lock:
            now = self.clock()
            if command and self.waiting_since is not None:
                self.waiting_since = None  # the command came after the phrase alone
                return "already_up"
            if not self.config.open_on_wake:
                return "off"
            if now < self.paused_until:
                return "paused"
            if not self.allowed():  # checked again now: never while locked or unknown
                return "locked"
            while self.opens and now - self.opens[0] > 3600:
                self.opens.popleft()
            recent = self.opens and now - self.opens[-1] < self.config.min_interval_s
            if recent or len(self.opens) >= self.config.per_hour:
                return "limit"
            # in flight: a wake meanwhile is "already_up" (after the phrase) or "limit"
            before = (self.opened_at, self.waiting_since)
            self.opens.append(now)
            self.opened_at = now
            self.waiting_since = None if command else now
        action = self.open_window()  # outside the lock: it may take seconds
        if action not in ("raised", "started", "browser"):
            with self._lock:  # nothing opened: as if it hadn't been tried
                if self.opens and self.opens[-1] == now:
                    self.opens.pop()
                self.opened_at, self.waiting_since = before
        return action

    def on_armed_expired(self) -> None:
        if self.waiting_since is not None:
            self.waiting_since = None
            self._false_wake()

    def check_closed(self) -> None:
        """Called `closed_quickly_s` after a wake-open: closed already means a false wake."""
        if self.opened_at is None:
            return
        quick = self.clock() - self.opened_at <= self.config.closed_quickly_s + 1
        if quick and not self.is_open():
            self.opened_at = None
            self._false_wake()

    def _false_wake(self) -> None:
        now = self.clock()
        self.false_wakes.append(now)
        while self.false_wakes and now - self.false_wakes[0] > self.config.false_wake_window_s:
            self.false_wakes.popleft()
        if len(self.false_wakes) >= self.config.false_wakes_for_pause:
            self.paused_until = now + self.config.pause_s
            self.false_wakes.clear()
