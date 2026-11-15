"""Talking to the brain (contract v0.2): health, the listener socket, frame helpers.

The listener never starts a brain. It connects to `/ws?wake=1&speak=1` on loopback,
which uses the server's reserved listener slot and the built-in `voice` bot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from sasha.listener.capture import CHUNK_BYTES, SAMPLE_RATE

log = logging.getLogger(__name__)

HEALTH_TIMEOUT_S = 3.0
CLOSE_SLOT_TAKEN = 1013  # another listener already holds the reserved slot: final


@dataclass(frozen=True)
class BrainHealth:
    ok: bool
    reason: str = ""


HealthCheck = Callable[[], Awaitable[BrainHealth]]


def http_health(base_url: str) -> HealthCheck:
    async def check() -> BrainHealth:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(HEALTH_TIMEOUT_S)) as client:
                body = (await client.get(f"{base_url}/api/health")).json()
        except (httpx.HTTPError, ValueError) as e:
            return BrainHealth(False, f"brain not reachable ({type(e).__name__})")
        return health_from(body)

    return check


def health_from(body: Any) -> BrainHealth:
    """Capture only when the brain can transcribe and accepts wake audio."""
    if not isinstance(body, dict) or not body.get("ok"):
        return BrainHealth(False, "brain not healthy")
    if not (body.get("voice") or {}).get("stt"):
        return BrainHealth(False, "brain has no speech-to-text")
    if not (body.get("wake") or {}).get("available"):
        return BrainHealth(False, "brain does not accept wake audio")
    return BrainHealth(True)


def listener_url(host: str, port: int) -> str:
    return f"ws://{host}:{port}/ws?wake=1&speak=1"


def frame(kind: str, **fields: Any) -> str:
    return json.dumps({"type": kind, **fields})


def segment_frames(pcm: bytes) -> Iterator[str | bytes]:
    """One gated segment as audio_start, 20 ms binary frames, audio_stop."""
    yield frame("audio_start", sample_rate=SAMPLE_RATE, wake=True)
    for start in range(0, len(pcm), CHUNK_BYTES):
        yield pcm[start : start + CHUNK_BYTES]
    yield frame("audio_stop")


async def writer(ws: Any, queue: asyncio.Queue[str | bytes]) -> None:
    """The one place the listener puts frames on the brain's WebSocket, in order.
    (Transport to the local brain only: nothing here sends messages for anyone.)
    A failed send is logged once and closes the socket, so the session ends and the
    listener reconnects instead of silently dropping frames."""
    try:
        while True:
            await ws.send(await queue.get())
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("Sending to the brain failed (%s); closing the socket", type(e).__name__)
        with contextlib.suppress(Exception):
            await ws.close()
