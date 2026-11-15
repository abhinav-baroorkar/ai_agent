"""The only file the listener may ever write: an opt-in dump of the segments it sends.

Enabled only by `sasha listen --debug-dump-audio` for that run, never from a profile.
Files go to `Data/debug-audio/<date>/` inside the project, readable by the owner only.
"""

from __future__ import annotations

import logging
import os
import wave
from datetime import datetime
from pathlib import Path

from sasha.listener.capture import SAMPLE_RATE
from sasha.listener.gate import Segment

log = logging.getLogger(__name__)


class DebugDump:
    def __init__(self, data_dir: Path) -> None:
        self.base = data_dir / "debug-audio"
        self.count = 0
        log.warning("Debug audio dump is ON for this run: segments are saved in %s", self.base)

    def __call__(self, segment: Segment) -> None:
        now = datetime.now()
        folder = self.base / f"{now:%Y-%m-%d}"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.count += 1
        path = folder / f"{now:%H%M%S}-{self.count:04d}.wav"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f, wave.open(f, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(segment.pcm)
