"""Microphone capture as a child process: raw 16 kHz mono s16le, read in 20 ms chunks.

The child is `arecord` (PipeWire's ALSA bridge) or, as a fallback, `pw-record` writing
to stdout. It is started through `setpriv --pdeathsig KILL` and a parent check, so it
dies with the listener even on a `kill -9`. Stopping kills it (the microphone is
closed, not muted). Nothing is written to disk: chunks go straight from the pipe to
the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
import shutil
import signal
import threading
from collections.abc import AsyncIterator, Callable, Sequence

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_MS = 20
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000  # 640
PR_SET_PDEATHSIG = 1

ARECORD = ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1"]
PW_RECORD = ["pw-record", "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16", "-"]


_SETPRIV = shutil.which("setpriv")
# Runs after setpriv has set the death signal: if the listener died in between, the
# child now has another parent (a subreaper such as the systemd user manager, not
# necessarily pid 1), so compare with the listener's real pid and refuse to record.
_PARENT_CHECK = 'test "$PPID" = "$1" || exit 127; shift; exec "$@"'
_LIBC = ctypes.CDLL("libc.so.6", use_errno=True)  # resolved once, in the parent
_PRCTL = _LIBC.prctl


def with_death_signal(command: Sequence[str]) -> tuple[list[str], Callable[[], None] | None]:
    """The command wrapped so the child gets SIGKILL when the listener dies.

    Preferred: `setpriv --pdeathsig KILL` plus a parent check, with no pre-exec hook
    (a Python `preexec_fn` can deadlock when other threads are alive). Fallback when
    setpriv is missing: a minimal pre-exec hook using the already-resolved prctl.

    Spawn only from the main (event-loop) thread, never from `asyncio.to_thread` or
    another worker: the death signal fires when the *thread* that forked the child
    exits, not the whole process, so a child forked by a short-lived worker thread
    would be killed as soon as that thread ends (or, with the setpriv parent check,
    refuse to start). This is checked here and refused."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Capture and playback children must be spawned from the main thread")
    parent = os.getpid()
    if _SETPRIV:
        script = ["/bin/sh", "-c", _PARENT_CHECK, "sh", str(parent)]
        return [_SETPRIV, "--pdeathsig", "KILL", "--", *script, *command], None

    def hook() -> None:  # pragma: no cover - only without setpriv
        if _PRCTL(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
            os._exit(127)

    return list(command), hook


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
        argv, hook = with_death_signal(self.command)
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            preexec_fn=hook,
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

    @property
    def live_or_starting(self) -> bool:
        """True from the moment start() is entered until kill() or exit: what the tray
        must treat as "microphone open" (a spawn in progress may open it any moment)."""
        return not self._killed and (self.proc is None or self.proc.returncode is None)

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
