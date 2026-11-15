"""Forget what the browser looked at, every time it stops (review nit, phase 1).

The profile under `Data/browser-profile` persists, so Chrome would keep every search
URL (the query is conversation text) and page visit on disk. DevTools has no command
that clears history, so after Chrome has exited (and again before it starts, in case
it was killed) the files that hold visited URLs are removed: history, sessions, the
cache, favicons, shortcuts and predictor data. Cookies, local storage and saved logins
are kept (sign-ins come in a later phase, with their own "Forget sign-ins" control).

Only inside our own marked profile (`profile.verify_profile`), only while no process
uses it, and never through a link: each entry's parent must resolve to exactly the
profile root plus its relative path (review H1).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sasha.browser.profile import FREE_WAIT_S, ProfileError, verify_profile, wait_until_free

log = logging.getLogger(__name__)

# Relative to the profile folder. Measured: after one search, the query was in History,
# Favicons, Sessions/ and Cache/; the rest hold visited URLs or hosts in longer runs.
URL_FILES = (
    *(
        f"Default/{name}"
        for name in (
            "History",
            "History-journal",
            "Visited Links",
            "Top Sites",
            "Top Sites-journal",
            "Shortcuts",
            "Shortcuts-journal",
            "Network Action Predictor",
            "Network Action Predictor-journal",
            "Favicons",
            "Favicons-journal",
            "Network Persistent State",
            "Reporting and NEL",
            "Reporting and NEL-journal",
            "DIPS",
            "DIPS-wal",
            "Sessions",
            "Sessions_Encrypted",
            "Session Storage",
            "Cache",
            "Code Cache",
        )
    ),
    "segmentation_platform",
)


def forget_history(profile: Path) -> int:
    """Remove the URL-bearing files from our own `profile`; returns how many were
    removed. The caller makes sure Chrome isn't running (`forget_when_free`)."""
    try:
        root = verify_profile(profile)
    except ProfileError as exc:
        log.warning("Browser history not cleared: %s", exc)
        return 0
    removed = 0
    for rel in URL_FILES:
        rel_path = Path(rel)
        parent = root / rel_path.parent
        try:
            if parent.resolve(strict=True) != parent:
                log.warning("Browser history not cleared under a link (%s)", rel_path.parent)
                continue
        except OSError:
            continue  # the parent folder doesn't exist
        path = parent / rel_path.name
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()  # a link is only removed, never followed
            elif path.is_dir():
                shutil.rmtree(path)  # doesn't follow links inside
            else:
                continue
            removed += 1
        except OSError as exc:
            log.warning("Couldn't remove browser history file (%s)", type(exc).__name__)
    return removed


async def forget_when_free(profile: Path, timeout: float = FREE_WAIT_S) -> int:
    """Wait (bounded) until nothing uses the profile, then forget; never under a live
    Chrome: if something still uses it, skip and log."""
    try:
        root = verify_profile(profile)
    except ProfileError as exc:
        log.warning("Browser history not cleared: %s", exc)
        return 0
    if not await wait_until_free(root, timeout):
        log.warning("Browser history not cleared: the profile is still in use")
        return 0
    return forget_history(root)
