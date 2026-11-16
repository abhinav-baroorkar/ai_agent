"""Finishing a take-over, also after a crash (review K1, K2, K3).

- A marker file (`browser-takeover.json`, 0600, `{"engine_site": ...}`) is written
  before the switch and removed only after the whole restore has succeeded. While it, or
  the configuration snapshot, exists, the next headless start runs this restore first,
  so a brain that stopped mid take-over leaves nothing of the owner's window behind.
- The restore: configuration back (C1), extension folders gone, the take-over files
  wiped, the sites' own storage wiped (K1: Local Storage, IndexedDB, service workers and
  the rest, so a sign-in kept without a cookie can't survive), and the usual history.
  Cookies are filtered separately at the next start (only the engine's, C3).
- Any failure raises `RestoreIncomplete`: the marker stays, and bots stay refused.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path

from sasha.browser.history import forget_history
from sasha.browser.profile import prepare_profile, wait_until_free
from sasha.browser.takeover_files import (
    clear_extensions,
    restore_config,
    wipe_takeover_files,
)

MARKER = "browser-takeover.json"
SNAPSHOT = "browser-takeover-config"
STORAGE_WIPE = (
    "Default/Local Storage",
    "Default/IndexedDB",
    "Default/Service Worker",
    "Default/Session Storage",
    "Default/File System",
    "Default/WebStorage",
    "Default/Shared Dictionary",
    "Default/Storage",
    "Default/blob_storage",
    "Default/GPUCache",
    "Default/Code Cache",
)


class RestoreIncomplete(RuntimeError):
    """The profile isn't back to the assistant's own yet (shown as is)."""


def marker_path(data_dir: Path) -> Path:
    return data_dir / MARKER


def snapshot_path(data_dir: Path) -> Path:
    return data_dir / SNAPSHOT


def write_marker(data_dir: Path, engine_site: str) -> None:
    """Atomically, 0600 from the start, never through a link."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = marker_path(data_dir)
    if path.is_symlink():
        raise RestoreIncomplete("The take-over marker is a link")
    tmp = data_dir / f".{MARKER}.tmp"
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"engine_site": engine_site}, f)
    os.replace(tmp, path)


def read_marker(data_dir: Path) -> str:
    """The engine's site from the marker ("" if none, unreadable, or no marker)."""
    path = marker_path(data_dir)
    try:
        if path.is_symlink():
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    site = data.get("engine_site") if isinstance(data, dict) else None
    return site if isinstance(site, str) and len(site) <= 255 else ""


def pending(data_dir: Path) -> bool:
    """A take-over hasn't been fully undone (it is running, or the brain stopped)."""
    return marker_path(data_dir).exists() or snapshot_path(data_dir).exists()


def wipe_storage(root: Path) -> None:
    """K1: every kind of site storage but cookies. Errors raise."""
    for rel in STORAGE_WIPE:
        path = root / rel
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


async def finish_takeover(data_dir: Path) -> str:
    """Undo everything the owner's window left; returns the site whose cookies the next
    start keeps ("" for none). Raises RestoreIncomplete, leaving the marker."""
    site = read_marker(data_dir)
    try:
        root = prepare_profile(data_dir)
        if not await wait_until_free(root):  # never touch the profile under a live Chrome
            raise RestoreIncomplete("The browser profile is still in use")
        snapshot = snapshot_path(data_dir)
        if snapshot.exists():
            restore_config(root, snapshot)
        clear_extensions(root)
        wipe_takeover_files(root)
        wipe_storage(root)
        forget_history(root)
    except RestoreIncomplete:
        raise
    except Exception as exc:
        raise RestoreIncomplete(
            f"The browser couldn't be cleaned up ({type(exc).__name__})"
        ) from None
    marker_path(data_dir).unlink(missing_ok=True)
    return site
