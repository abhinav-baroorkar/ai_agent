"""Did the app shell really start? (found on the owner's display, 2026-11-16)

Spawning the shell isn't proof that a window appeared: on Ubuntu without the
`libxcb-cursor0` package, Qt 6.5+ aborts at once ("xcb-cursor0 or libxcb-cursor0 is
needed to load the Qt xcb platform plugin"), and the launcher used to say "Opened".
So after the spawn:
- the shell's first output goes to `Data/logs/app-shell-start.log` (0600, replaced at
  every start; the shell silences its own stderr once its window is up, so later
  output never lands there);
- `confirm` waits until the single-instance socket answers `ping`, or the process has
  ended (failed), or `START_CHECK_S` passed with it still alive (slow, but up);
- `failure_reason` reads the log's first lines and names a known cause in plain words.
Nothing secret is expected there: Qt prints plugin and display errors at start. The
file itself is written as is (0600, beside the token file in Data/); a token-like value
is masked when it is shown (`first_lines`), in the terminal or a notification.
"""

from __future__ import annotations

import ctypes.util
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

from sasha.config import Settings

START_CHECK_S = 10.0
POLL_S = 0.2
LOG_NAME = "app-shell-start.log"
MAX_READ = 8192
MISSING_CURSOR = "a system package is missing: libxcb-cursor0"
APT_HINT = "sudo apt install libxcb-cursor0"
_TOKENISH = re.compile(r"(t=|token[=:. ]\s*)[0-9a-fA-F]{8,}|[0-9a-fA-F]{32,}")


def log_path(settings: Settings) -> Path:
    return settings.paths.data_dir / "logs" / LOG_NAME


def open_log(settings: Settings) -> int:
    """A fresh 0600 log for the shell's stderr (a file descriptor for the spawn)."""
    path = log_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def quiet_stderr(fd: int = 2) -> None:
    """Point `fd` (the shell's stderr) at /dev/null: called once its window is up, so
    later output never lands in the start log."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, fd)
    finally:
        os.close(devnull)


def confirm(
    ping: Callable[[], bool],
    alive: Callable[[], bool],
    timeout: float = START_CHECK_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True once the window answers `ping`, or if it is still alive at the timeout."""
    end = clock() + timeout
    while clock() < end:
        if ping():
            return True
        if not alive():
            return ping()  # it may have handed over to a window that was already open
        sleep(POLL_S)
    return alive()


def first_lines(path: Path, lines: int = 20) -> str:
    try:
        with path.open("rb") as f:
            text = f.read(MAX_READ).decode("utf-8", "replace")
    except OSError:
        return ""
    return _TOKENISH.sub("<hidden>", "\n".join(text.splitlines()[:lines]))


def failure_reason(text: str) -> str:
    """A known cause in plain words, from the shell's first output."""
    low = text.lower()
    if "xcb-cursor" in low:
        return MISSING_CURSOR
    if "could not connect to display" in low or "cannot open display" in low:
        return "it couldn't reach the screen"
    if "no module named" in low:
        return "a part of the app extra is missing; run `uv sync --extra app`"
    return "it stopped as soon as it started"


def failure_message(reason: str, fallback: bool = True) -> str:
    tail = " Opened the browser window instead." if fallback else ""
    return f"The app window couldn't start ({reason}).{tail}"


def cursor_library_present(find: Callable[[str], str | None] = ctypes.util.find_library) -> bool:
    """The system library Qt's X11 plugin needs since Qt 6.5 (for `sasha doctor`)."""
    return find("xcb-cursor") is not None
