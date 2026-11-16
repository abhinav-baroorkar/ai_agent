"""One window only, and how others ask it to come up (app-shell design 6).

The running shell listens on a Unix socket in $XDG_RUNTIME_DIR (a 0700 folder of this
user). A second launch, or the listener on a wake, connects and sends one line:
`raise` or `wake`, optionally followed by a desktop activation token (from a menu click
or a notification click), which lets the desktop really raise the window. Plain
`socket` on the client side, so the listener needs no Qt.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import socket
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SOCKET_NAME = "assistant-window.sock"
DESKTOP_ID = "assistant-window"  # the window class and the menu entry's file name
MAX_LINE = 256  # a command plus an activation token
READ_TIMEOUT_MS = 2000
COMMANDS = frozenset({"raise", "wake", "ping"})  # ping: "are you open?", nothing shown
_TOKEN = re.compile(r"[\w.:-]{1,200}")


class NoRuntimeDir(RuntimeError):
    """No $XDG_RUNTIME_DIR: no private place for the socket, so no window (no /tmp)."""


def socket_path() -> Path:
    """The socket in $XDG_RUNTIME_DIR, which must be a folder of this user that no one
    else can enter (0700 as logind makes it); anything else is refused, never /tmp."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise NoRuntimeDir("XDG_RUNTIME_DIR is not set; the window needs a desktop session")
    try:
        info = os.stat(runtime)
    except OSError:
        raise NoRuntimeDir("XDG_RUNTIME_DIR doesn't exist") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise NoRuntimeDir("XDG_RUNTIME_DIR isn't a private folder of this user")
    return Path(runtime) / SOCKET_NAME


def ask_window(path: Path, command: str, timeout: float = 1.0) -> bool:
    """Ask a running shell to come up; False if none answers."""
    if command not in COMMANDS:
        raise ValueError(command)
    token = os.environ.get("XDG_ACTIVATION_TOKEN") or os.environ.get("DESKTOP_STARTUP_ID")
    line = command + (f" {token}" if token and _TOKEN.fullmatch(token) else "") + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(line.encode())
            return s.recv(8) == b"ok\n"
    except OSError:
        return False


def parse(line: bytes) -> tuple[str, str | None] | None:
    words = line.decode(errors="replace").split()
    if not words or words[0] not in COMMANDS or len(words) > 2:
        return None
    token = words[1] if len(words) == 2 and _TOKEN.fullmatch(words[1]) else None
    return words[0], token


class SingleInstance:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._server: Any = None

    def hand_over(self, command: str) -> bool:
        return ask_window(self.path, command)

    def serve(self, app: Any, on_command: Callable[[str], None]) -> None:
        """Listen (Qt event loop); a stale socket file from a crashed shell is removed
        first, since `hand_over` found no one answering on it."""
        from PySide6.QtCore import QTimer
        from PySide6.QtNetwork import QLocalServer

        with contextlib.suppress(FileNotFoundError):
            if self.path.is_socket():
                self.path.unlink()
        server = QLocalServer(app)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(str(self.path)):
            log.warning("Window socket not available; a second launch may open a second window")
            return

        def accept() -> None:
            conn = server.nextPendingConnection()
            if conn is None:
                return

            conn.setReadBufferSize(MAX_LINE)  # a caller can't make us buffer more
            QTimer.singleShot(READ_TIMEOUT_MS, conn.abort)  # nor hold the connection open

            def read() -> None:
                if not conn.canReadLine():
                    if conn.bytesAvailable() >= MAX_LINE:
                        conn.abort()  # a full buffer and still no line: not ours
                    return
                got = parse(bytes(conn.readLine(MAX_LINE).data()))
                conn.write(b"ok\n" if got else b"no\n")
                conn.flush()
                conn.disconnectFromServer()
                if got and got[0] != "ping":
                    command, token = got
                    if token:  # lets the desktop raise us (Wayland, X11 startup id)
                        os.environ["XDG_ACTIVATION_TOKEN"] = token
                    on_command(command)

            conn.readyRead.connect(read)

        server.newConnection.connect(accept)
        self._server = server

    def close(self) -> None:
        if self._server is not None:
            self._server.close()


def notify(text: str, run: Callable[..., Any] = subprocess.run) -> None:
    """A desktop notification; clicking it opens the app through its menu entry (which
    the desktop starts with an activation token, so the window really comes up)."""
    cmd = [
        "gdbus", "call", "--session",
        "--dest", "org.freedesktop.Notifications",
        "--object-path", "/org/freedesktop/Notifications",
        "--method", "org.freedesktop.Notifications.Notify",
        DESKTOP_ID, "0", DESKTOP_ID, text, "", "['default', 'Open']",
        f"{{'desktop-entry': <'{DESKTOP_ID}'>}}", "5000",
    ]  # fmt: skip
    try:
        run(cmd, check=False, timeout=3, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Notification not shown (%s)", type(exc).__name__)
