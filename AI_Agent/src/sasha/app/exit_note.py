"""Why the app shell ended, written to its start log (2026-11-16: it ended, nothing said).

The shell's stderr goes to `Data/logs/app-shell-start.log` until it silences itself
(start_check.quiet_stderr). A copy of that descriptor is kept for these notes only: our
own fixed lines, never Qt's output or the page's text. Every way out leaves one line:
- it handed over to a window that was already open;
- the window was closed (by the person or the desktop);
- a signal (SIGTERM, SIGHUP, SIGINT): systemd counts these as a clean exit, so the
  journal shows nothing but the CPU time;
- a crash: faulthandler writes the Python stack there;
- the event loop's exit code.
"""

from __future__ import annotations

import contextlib
import faulthandler
import os
import signal
import time
from collections.abc import Callable
from typing import Any

PREFIX = "[shell]"
HANDOVER = "handed over to the window that was already open"
CLOSED = "the window was closed"
STOPPED = "stopped by"
SIGNALS = ("SIGTERM", "SIGHUP", "SIGINT")
TICK_MS = 500  # lets Python run a signal handler while Qt's event loop waits


class ExitNotes:
    def __init__(self, fd: int = 2, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.started = clock()
        self.stopping = False
        try:
            self.fd = os.dup(fd)  # survives quiet_stderr pointing fd 2 at /dev/null
        except OSError:
            self.fd = -1

    def write(self, reason: str) -> None:
        if self.fd < 0:
            return
        line = f"{PREFIX} {reason} ({self.clock() - self.started:.1f} s after start)\n"
        with contextlib.suppress(OSError):
            os.write(self.fd, line.encode())

    def catch_crashes(self) -> None:
        """A segfault or abort in Qt or Chromium writes the Python stack to the log."""
        if self.fd >= 0:
            faulthandler.enable(self.fd, all_threads=True)

    def watch(self, app: Any) -> None:
        """Note a closed window and a stop signal (then quit cleanly). Qt imports here."""
        from PySide6.QtCore import QTimer

        def closed() -> None:
            if not self.stopping:  # a signal's quit closes the window too: said already
                self.write(CLOSED)

        app.lastWindowClosed.connect(closed)

        def stop(signum: int, _frame: Any) -> None:
            self.stopping = True
            self.write(f"{STOPPED} {signal.Signals(signum).name}")
            app.quit()

        for name in SIGNALS:
            signal.signal(getattr(signal, name), stop)
        tick = QTimer()  # kept on self: it lives as long as the notes
        tick.timeout.connect(lambda: None)  # Python checks for signals between calls
        tick.start(TICK_MS)
        self._tick = tick

    def ended(self, code: int) -> None:
        self.write(f"ended (exit code {code})")
