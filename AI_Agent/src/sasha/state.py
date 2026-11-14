"""Small JSON files under `Data/state/` (git-ignored) for things the UI can change.

Writes are atomic (temp file + rename) and serialised with a lock, so a crash or two
requests at once can't leave a half-written file. Secrets never go in here.
"""

from __future__ import annotations

import json
import logging
import os
import threading
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
            aside = self.path.with_suffix(self.path.suffix + ".corrupt")
            log.warning("State file %s is unreadable; moved it to %s", self.path, aside.name)
            os.replace(self.path, aside)
            return {}

    def save(self, data: dict[str, Any]) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
