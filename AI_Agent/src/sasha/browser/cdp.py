"""A small DevTools-protocol client over Chrome's `--remote-debugging-pipe`.

Chrome reads commands from its fd 3 and writes replies and events to its fd 4, each
message JSON followed by a NUL byte. A pipe is reachable only by the process that
started Chrome; there is never a TCP debugging port (review B6).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

MESSAGE_LIMIT = 64 * 1024 * 1024  # accessibility trees and screencast frames are large
CALL_TIMEOUT_S = 10.0

Listener = Callable[[dict[str, Any]], None]


class CDPError(RuntimeError):
    """A DevTools call failed. The message is the protocol's, never page content."""


class CDPPipe:
    def __init__(self, read_fd: int, write_fd: int) -> None:
        self._read_fd, self._write_fd = read_fd, write_fd
        self._reader: asyncio.StreamReader | None = None
        self._transport: asyncio.WriteTransport | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._listeners: dict[str, list[Listener]] = {}
        self._task: asyncio.Task[None] | None = None
        self.closed = asyncio.Event()

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=MESSAGE_LIMIT)
        rfile = os.fdopen(self._read_fd, "rb", buffering=0)
        wfile = os.fdopen(self._write_fd, "wb", buffering=0)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), rfile)
        transport, _ = await loop.connect_write_pipe(asyncio.Protocol, wfile)
        self._reader, self._transport = reader, transport
        self._task = asyncio.create_task(self._read_loop())

    def on(self, method: str, listener: Listener) -> None:
        """Call `listener(params_with_session)` for every event named `method`."""
        self._listeners.setdefault(method, []).append(listener)

    def off(self, method: str, listener: Listener) -> None:
        with contextlib.suppress(KeyError, ValueError):
            self._listeners[method].remove(listener)

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float = CALL_TIMEOUT_S,
    ) -> dict[str, Any]:
        if self._transport is None or self.closed.is_set():
            raise CDPError("DevTools pipe is closed")
        message: dict[str, Any] = {"id": next(self._ids), "method": method, "params": params or {}}
        if session_id is not None:
            message["sessionId"] = session_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message["id"]] = future
        self._transport.write(json.dumps(message).encode() + b"\0")
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as e:
            raise CDPError(f"{method} timed out") from e
        finally:
            self._pending.pop(message["id"], None)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                raw = await self._reader.readuntil(b"\0")
                self._dispatch(json.loads(raw[:-1]))
        except (
            asyncio.IncompleteReadError,
            ConnectionError,
            ValueError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            self.closed.set()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CDPError("DevTools pipe closed"))

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "id" in message:
            future = self._pending.get(int(message["id"]))
            if future is None or future.done():
                return
            if "error" in message:
                error = message["error"]
                future.set_exception(CDPError(str(error.get("message", "error"))[:200]))
            else:
                future.set_result(message.get("result", {}))
            return
        method = message.get("method")
        if not isinstance(method, str):
            return
        params = dict(message.get("params") or {})
        if "sessionId" in message:
            params["_session"] = message["sessionId"]
        for listener in self._listeners.get(method, []):
            try:
                listener(params)
            except Exception as e:  # one bad listener must not stop the pipe
                log.warning("DevTools event handler failed (%s)", type(e).__name__)

    async def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.closed.set()
