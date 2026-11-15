"""Forget what the browser looked at, every time it stops (review nit, phase 1).

The profile under `Data/browser-profile` persists, so Chrome would keep every search
URL (the query is conversation text) and page visit on disk. DevTools has no command
that clears history, so after Chrome has exited (and again before it starts, in case
it was killed) the files that hold visited URLs are removed: history, sessions, the
cache, favicons, shortcuts and predictor data. Cookies, local storage and saved logins
are kept (sign-ins come in a later phase, with their own "Forget sign-ins" control).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

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
    """Remove the URL-bearing files from `profile`; returns how many were removed.
    Never follows a link out of the profile."""
    removed = 0
    for rel in URL_FILES:
        path = profile / rel
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                continue
            removed += 1
        except OSError as exc:
            log.warning("Couldn't remove browser history file (%s)", type(exc).__name__)
    return removed
