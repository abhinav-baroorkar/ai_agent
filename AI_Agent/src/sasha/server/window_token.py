"""A per-launch token for the window (take-over design 9.1, review C4).

The brain makes a random token when it starts and writes it, readable by its own user
only, to `Data/state/window-token`. The window launcher reads it and hands it to the
window through a start page in the window's private profile folder (never on a command
line, which other users can read in /proc). The window sends it back as the header
`X-Window-Token` on HTTP and as the WebSocket subprotocol `token.<hex>` (the server
answers `brain.v1`), so it never sits in a URL that could reach a log.

Scope, accepted: any process of the same user can read the file (it could read the
profile and ptrace anyway). The token stops web pages and other users' processes.
Required today on `/api/browser*` and `/ws/browser`; later on the other routes too.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import re
import secrets
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

TOKEN_FILE = "window-token"
START_PAGE = "start.html"
HEADER = "x-window-token"
PROTOCOL = "brain.v1"
PREFIX = "token."
_HEX = re.compile(r"[0-9a-f]{64}")


class WindowToken:
    def __init__(self, value: str | None = None) -> None:
        self.value = value or secrets.token_hex(32)

    def matches(self, given: str | None) -> bool:
        """Constant-time comparison; never logs or echoes the value."""
        if not given or not _HEX.fullmatch(given):
            return False
        return hmac.compare_digest(given.encode(), self.value.encode())

    def in_subprotocols(self, protocols: Iterable[str]) -> bool:
        offered = list(protocols)
        if PROTOCOL not in offered:
            return False
        return any(p.startswith(PREFIX) and self.matches(p[len(PREFIX) :]) for p in offered)


def write_private(path: Path, data: bytes) -> Path:
    """Write `data` atomically, 0600 from the start (no chmod after the write), into a
    folder made private (0700): a temporary file opened O_EXCL|O_NOFOLLOW, then replace."""
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.stat().st_mode & 0o077:
        os.chmod(folder, 0o700)  # the folder only; the file is never loose
    tmp = folder / f".{path.name}.{os.getpid()}.tmp"
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return path


def write_token(state_dir: Path, token: WindowToken) -> Path:
    return write_private(state_dir / TOKEN_FILE, token.value.encode())


def write_start_page(profile_dir: Path, url: str, token: str) -> Path:
    """A local page that forwards the window to `url#t=<token>`. The window is opened on
    this file, so the token never appears on a command line (/proc is readable by
    other users); the file sits in the window's 0700 profile folder, 0600."""
    target = json.dumps(f"{url.rstrip('/')}/#t={token}")
    html = (
        "<!doctype html><meta charset=utf-8><title>Opening…</title>"
        f"<script>location.replace({target});</script>"
    )
    return write_private(profile_dir / START_PAGE, html.encode())


def read_token(state_dir: Path) -> str | None:
    """The running brain's token, if its file is a plain file of ours, 0600, well formed."""
    path = state_dir / TOKEN_FILE
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            log.warning("Window token file ignored: not private to this user")
            return None
        value = os.read(fd, 128).decode(errors="replace").strip()
    finally:
        os.close(fd)
    return value if _HEX.fullmatch(value) else None


def remove_token(state_dir: Path, token: WindowToken) -> None:
    """Remove the file when the brain stops, only if it still holds our own token (a
    newer brain may have replaced it)."""
    if read_token(state_dir) == token.value:
        with contextlib.suppress(OSError):
            (state_dir / TOKEN_FILE).unlink()
