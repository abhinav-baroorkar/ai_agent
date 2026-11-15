"""Play speech and earcons: `aplay -q -t raw -f S16_LE -c 1 -r <rate>` fed on stdin.

Each reply (or earcon) gets its own short-lived child with PR_SET_PDEATHSIG, so a
crashed listener never leaves a player behind. `finish()` waits until the audio has
actually been played, which the half-duplex rule needs (the microphone stays closed
until 300 ms after the end of playback, not the end of the network stream).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from sasha.listener.capture import die_with_parent

log = logging.getLogger(__name__)

PlayerCommand = Callable[[int], list[str]]


def aplay_command(sample_rate: int) -> list[str]:
    return ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", str(sample_rate)]


class Player:
    def __init__(self, command: PlayerCommand = aplay_command) -> None:
        self.command = command
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def playing(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self, sample_rate: int) -> None:
        await self.stop()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command(sample_rate),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                preexec_fn=die_with_parent,
            )
        except OSError as e:
            log.warning("Playback could not start (%s)", type(e).__name__)
            self._proc = None

    async def write(self, pcm: bytes) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            return
        try:
            proc.stdin.write(pcm)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            log.warning("Playback stopped early")

    async def finish(self, timeout_s: float = 120.0) -> None:
        """Close the input and wait until everything has been played."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.stdin is not None:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.close()
                await proc.stdin.wait_closed()
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
        except asyncio.TimeoutError:
            proc.kill()

    async def stop(self) -> None:
        """Cut playback off now."""
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 2.0)

    async def play(self, pcm: bytes, sample_rate: int) -> None:
        """One clip from start to end (earcons)."""
        await self.start(sample_rate)
        await self.write(pcm)
        await self.finish(timeout_s=10.0)
