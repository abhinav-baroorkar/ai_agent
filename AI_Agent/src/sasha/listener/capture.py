"""Microphone capture as a child process: raw 16 kHz mono s16le, read in 20 ms chunks.

The child is `arecord` (PipeWire's ALSA bridge) or, as a fallback, `pw-record` writing
to stdout. It gets `PR_SET_PDEATHSIG=SIGKILL`, so it dies with the listener even on a
`kill -9`. Stopping kills it (the microphone is closed, not muted). Nothing is written
to disk: chunks go straight from the pipe to the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
import shutil
import signal
from collections.abc import AsyncIterator, Callable, Sequence

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_MS = 20
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000  # 640
PR_SET_PDEATHSIG = 1

ARECORD = ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1"]
PW_RECORD = ["pw-record", "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16", "-"]


def die_with_parent() -> None:
    """Pre-exec hook (runs in the child): get SIGKILL when the listener dies."""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
        os._exit(127)  # never record without the guarantee
    if os.getppid() == 1:  # the parent died before prctl took effect
        os._exit(127)


def capture_command(which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    """`arecord` if present, else `pw-record`, else None (no capture possible)."""
    if which("arecord"):
        return list(ARECORD)
    if which("pw-record"):
        return list(PW_RECORD)
    return None


def strip_riff(head: bytes) -> bytes:
    """If a WAV header starts the stream, drop it (up to the start of the data chunk)."""
    if not head.startswith(b"RIFF") or b"data" not in head[:4096]:
        return head
    at = head.index(b"data", 12)
    return head[at + 8 :]  # "data" + 4-byte size


class CaptureProcess:
    """One running capture child. `chunks()` yields 640-byte chunks until it ends."""

    def __init__(self, command: Sequence[str]) -> None:
        self.command = list(command)
        self.proc: asyncio.subprocess.Process | None = None
        self._pid: int | None = None
        self._killed = False

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            preexec_fn=die_with_parent,
            start_new_session=False,
        )
        self._pid = self.proc.pid

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def running(self) -> bool:
        """False as soon as `kill()` was called (SIGKILL can't be ignored), even before
        the child has been reaped."""
        return self.proc is not None and self.proc.returncode is None and not self._killed

    async def chunks(self) -> AsyncIterator[bytes]:
        assert self.proc is not None and self.proc.stdout is not None
        stdout = self.proc.stdout
        first = True
        pending = b""
        while True:
            try:
                data = await stdout.read(CHUNK_BYTES * 4)
            except (ConnectionError, ValueError):
                return
            if not data:
                return
            if first:
                data, first = strip_riff(data), False
            pending += data
            while len(pending) >= CHUNK_BYTES:
                yield pending[:CHUNK_BYTES]
                pending = pending[CHUNK_BYTES:]

    def kill(self) -> None:
        """Close the microphone now. Safe to call from any thread, any number of times."""
        self._killed = True
        pid = self._pid
        if pid is not None and (self.proc is None or self.proc.returncode is None):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)

    async def stop(self) -> None:
        self.kill()
        if self.proc is not None:
            with contextlib.suppress(ProcessLookupError):
                await asyncio.wait_for(self.proc.wait(), timeout=2.0)


class Backoff:
    """Restart delays: 0.5, 1, 2, 5, 10 s, then 10 s; reset after a good run."""

    STEPS = (0.5, 1.0, 2.0, 5.0, 10.0)

    def __init__(self) -> None:
        self.failures = 0

    def next_delay(self) -> float:
        delay = self.STEPS[min(self.failures, len(self.STEPS) - 1)]
        self.failures += 1
        return delay

    def reset(self) -> None:
        self.failures = 0
