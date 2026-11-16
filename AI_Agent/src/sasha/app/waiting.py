"""Waiting for the brain (`sasha window --attach`, the window started at login).

At login the window and the brain service start at about the same time. The window
must never start a brain of its own (the service would then fail on the port), so it
opens at once with a plain "starting…" line and loads the page once the brain's
health check answers. After `ATTACH_WAIT_S` it says the brain didn't start, and keeps
checking slowly, so a brain that comes up late still loads. Pure Python, no Qt.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Callable

ATTACH_WAIT_S = 60.0
POLL_S = 0.5
SLOW_POLL_S = 5.0  # after the wait is over

_no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def brain_up(port: int, timeout_s: float = 0.5) -> bool:
    """The brain's own health check on loopback (no proxy, whatever the environment)."""
    try:
        with _no_proxy.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout_s) as resp:
            return resp.status == 200 and json.loads(resp.read(65536)).get("ok") is True
    except (OSError, ValueError, AttributeError):
        return False


class BrainWait:
    """Checks in a background thread; the window asks `state()` from its own timer:
    "waiting", "up" or "late" (the wait is over; still checking)."""

    def __init__(
        self,
        check: Callable[[], bool],
        wait_s: float = ATTACH_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.check = check
        self.clock = clock
        self.sleep = sleep
        self.deadline = clock() + wait_s
        self.up = threading.Event()
        self.stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def state(self) -> str:
        if self.up.is_set():
            return "up"
        return "late" if self.clock() >= self.deadline else "waiting"

    def run(self) -> None:
        while not self.stopped.is_set():
            if self.check():
                self.up.set()
                return
            self.sleep(SLOW_POLL_S if self.clock() >= self.deadline else POLL_S)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="brain-wait", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stopped.set()
