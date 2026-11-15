"""Capture child: chunking, WAV header, kill, backoff, and death with the parent."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time

from sasha.listener.capture import (
    ARECORD,
    CHUNK_BYTES,
    PW_RECORD,
    Backoff,
    CaptureProcess,
    capture_command,
    strip_riff,
)
from tests.conftest import REPO_ROOT
from tests.listener_fakes import fake_mic_command


def test_capture_command_prefers_arecord() -> None:
    assert capture_command(lambda n: f"/usr/bin/{n}") == ARECORD
    assert capture_command(lambda n: "/usr/bin/pw-record" if n == "pw-record" else None) == (
        PW_RECORD
    )
    assert capture_command(lambda n: None) is None
    assert ARECORD == ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]


def test_strip_riff() -> None:
    header = b"RIFF\xff\xff\xff\xffWAVEfmt \x10\0\0\0" + b"\0" * 16 + b"data\xff\xff\xff\xff"
    assert strip_riff(header + b"pcm") == b"pcm"
    assert strip_riff(b"\x01\x02raw") == b"\x01\x02raw"


async def _read(proc: CaptureProcess, n: int) -> list[bytes]:
    got: list[bytes] = []
    async for chunk in proc.chunks():
        got.append(chunk)
        if len(got) == n:
            break
    return got


def test_chunks_are_20_ms_and_kill_closes_the_child() -> None:
    async def main() -> None:
        for riff in (False, True):
            proc = CaptureProcess(fake_mic_command(riff=riff))
            await proc.start()
            got = await asyncio.wait_for(_read(proc, 5), 5)
            assert [len(c) for c in got] == [CHUNK_BYTES] * 5
            assert got[0] == b"\x01\x00" * 320  # header gone, samples aligned
            await proc.stop()
            assert not proc.running
            proc.kill()  # twice is harmless

    asyncio.run(main())


def test_backoff_steps_and_reset() -> None:
    b = Backoff()
    assert [b.next_delay() for _ in range(7)] == [0.5, 1, 2, 5, 10, 10, 10]
    b.reset()
    assert b.next_delay() == 0.5


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f"/proc/{pid}/stat") as f:  # a zombie is dead for our purpose
        return f.read().split()[2] != "Z"


def test_kill_9_of_the_listener_leaves_no_capture_child() -> None:
    """PR_SET_PDEATHSIG: the capture child dies within 1 s of a `kill -9` of its parent."""
    code = textwrap.dedent(
        f"""
        import asyncio, sys
        sys.path.insert(0, {str(REPO_ROOT / "AI_Agent")!r})
        from sasha.listener.capture import CaptureProcess
        from tests.listener_fakes import fake_mic_command
        async def main():
            proc = CaptureProcess(fake_mic_command(seconds=60))
            await proc.start()
            print(proc.pid, flush=True)
            await asyncio.sleep(60)
        asyncio.run(main())
        """
    )
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert parent.stdout is not None
        child = int(parent.stdout.readline())
        assert _alive(child)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(5)
        deadline = time.monotonic() + 1.0
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _alive(child), "capture child survived its parent"
    finally:
        if parent.poll() is None:
            parent.kill()
