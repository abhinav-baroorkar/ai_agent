"""Small JSON files under `Data/state/` (git-ignored) for things the UI can change.

Writes are atomic (temp file + rename) and serialised with a lock, so a crash or two
requests at once can't leave a half-written file. Secrets never go in here.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        """The stored mapping, or `{}` if the file is missing or unreadable.

        A corrupt file is moved aside (`<name>.corrupt`) rather than overwritten, so
        nothing is lost silently.
        """
        with self.lock:
            if not self.path.is_file():
                return {}
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict):
                return data
            # Timestamped to the nanosecond, so a later corruption never overwrites this one.
            stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"
            aside = self.path.with_suffix(f"{self.path.suffix}.{stamp}.corrupt")
            log.warning("State file %s is unreadable; moved it to %s", self.path, aside.name)
            os.replace(self.path, aside)
            return {}

    def save(self, data: dict[str, Any]) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2))
                f.flush()
                os.fsync(f.fileno())  # on disk before the rename, so a crash can't empty it
            os.replace(tmp, self.path)
