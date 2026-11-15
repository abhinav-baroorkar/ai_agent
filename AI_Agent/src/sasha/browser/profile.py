"""The browser's own profile folder: made by us, never a link, never in use when wiped.

`Data/browser-profile` must be a real folder (not a symlink) holding the marker file
`.sasha-browser-profile`, which is written only into a folder we have just created or
found empty. Without it the browser refuses to start and nothing is deleted, so a
profile folder pointed at the owner's real Chrome profile (or any other folder) is
never used or wiped (review H1). A folder left from before the marker existed is
refused too; the owner resets it by removing `Data/browser-profile`.

Before history files are removed, `wait_until_free` waits (bounded) until no process
works in the profile and its `SingletonLock` names no live process (review H2).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

MARKER = ".sasha-browser-profile"
FOLDER = "browser-profile"
FREE_WAIT_S = 5.0


class ProfileError(RuntimeError):
    """The profile folder isn't one this assistant made; refuse to use or wipe it."""


def profile_dir(data_dir: Path) -> Path:
    return data_dir / FOLDER


def _reset_hint(path: Path) -> str:
    return f"To reset the browser, stop the brain and remove the folder {path}"


def verify_profile(path: Path) -> Path:
    """The resolved profile root, if `path` is our own marked folder; else ProfileError."""
    if path.is_symlink():
        raise ProfileError(f"The browser profile {path} is a link; refusing. {_reset_hint(path)}")
    if not path.is_dir():
        raise ProfileError(f"The browser profile {path} doesn't exist")
    marker = path / MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ProfileError(
            f"The browser profile {path} wasn't made by the assistant (no marker); "
            f"refusing to use or clean it. {_reset_hint(path)}"
        )
    return path.resolve(strict=True)


def prepare_profile(data_dir: Path) -> Path:
    """Create the profile (with its marker) if missing or empty, then verify it."""
    path = profile_dir(data_dir)
    if not path.is_symlink():
        if not path.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            path.mkdir(mode=0o700)
        if path.is_dir() and not any(path.iterdir()):
            (path / MARKER).write_text("Made by the assistant's browser; safe to delete.\n")
    return verify_profile(path)


def _pids() -> list[int]:
    return [int(p) for p in os.listdir("/proc") if p.isdigit()]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def in_use(root: Path) -> bool:
    """True while any process has its working directory in the profile or was started
    with it as `--user-data-dir`, or the profile's SingletonLock names a live pid."""
    flag = f"--user-data-dir={root}"
    for pid in _pids():
        with contextlib.suppress(OSError):
            cwd = Path(os.readlink(f"/proc/{pid}/cwd"))
            if cwd == root or root in cwd.parents:
                return True
        with contextlib.suppress(OSError):
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if any(a.decode(errors="replace").startswith(flag) for a in args):
                return True
    with contextlib.suppress(OSError, ValueError):
        target = os.readlink(root / "SingletonLock")  # "<host>-<pid>"
        return _alive(int(target.rsplit("-", 1)[1]))
    return False


async def wait_until_free(root: Path, timeout: float = FREE_WAIT_S) -> bool:
    deadline = time.monotonic() + timeout
    while in_use(root):
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.1)
    return True
