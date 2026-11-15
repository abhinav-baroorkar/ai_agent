"""The filtering proxy every browser request goes through (asyncio, no dependencies).

Chrome is started with `--proxy-server=http://127.0.0.1:<port>`, a bypass list of
`<-loopback>` (so even loopback goes through here) and a resolver rule that makes it
resolve nothing itself. This proxy then:
- accepts `CONNECT host:port` (HTTPS) and absolute-form `GET http://host/...` (HTTP);
- resolves the name once, refuses unless every address passes the guard, and connects
  to the vetted address itself, so DNS rebinding between check and connect can't work;
- answers a refusal with `403` and counts it by category.
It logs and keeps counts only: never hosts, URLs, queries or bodies (review B5).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sasha.browser.guard import IP, Resolver, resolve, system_resolver

log = logging.getLogger(__name__)

HEAD_LIMIT = 16 * 1024
HEAD_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 10.0
IDLE_TIMEOUT_S = 120.0
MAX_CONNECTIONS = 256
MAX_BODY = 1024 * 1024  # phase 1 is read-only; a larger request body is refused
# Chrome's own background endpoints seen from a fresh profile (account list, component
# update) that no page needs. Exact hostnames, not domains, so www.google.com search
# pages and www.gstatic.com page assets still work. Chrome tolerates these failing.
BACKGROUND_HOSTS = frozenset({"accounts.google.com", "clients2.google.com"})
_HOP_HEADERS = {b"proxy-connection", b"proxy-authorization", b"connection", b"keep-alive"}


@dataclass
class ProxyStats:
    """Counts only. `refused` is by category (loopback, private, port, ...)."""

    allowed: int = 0
    refused: Counter[str] = field(default_factory=Counter)
    background_refused: int = 0
    bad_requests: int = 0
    over_capacity: int = 0
    bytes_in: int = 0
    bytes_out: int = 0


class FilteringProxy:
    def __init__(
        self,
        resolver: Resolver = system_resolver,
        max_connections: int = MAX_CONNECTIONS,
        refuse_hosts: frozenset[str] = BACKGROUND_HOSTS,
    ) -> None:
        self.resolver = resolver
        self.refuse_hosts = refuse_hosts
        self.max_connections = max_connections
        self.stats = ProxyStats()
        self._server: asyncio.base_events.Server | None = None
        self._active = 0

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._active += 1
        try:
            if self._active > self.max_connections:
                self.stats.over_capacity += 1
                return await self._reply(writer, 503, "busy")
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
            await self._serve(head, reader, writer)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            self.stats.bad_requests += 1
        except ValueError:  # e.g. a port out of range in the URL
            self.stats.bad_requests += 1
            with contextlib.suppress(Exception):
                await self._reply(writer, 400, "")
        except (ConnectionError, OSError):
            pass
        finally:
            self._active -= 1
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _serve(
        self, head: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if len(head) > HEAD_LIMIT:
            return await self._reply(writer, 431, "bad_request")
        line, _, rest = head.partition(b"\r\n")
        try:
            method, target, version = line.decode("latin-1").split(" ")
        except ValueError:
            return await self._reply(writer, 400, "bad_request")
        if method == "CONNECT":
            host, _, port_text = target.rpartition(":")
            port = int(port_text) if port_text.isdigit() else -1
            upstream = await self._open(host.strip("[]"), port, writer)
            if upstream is None:
                return None
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            return await self._pipe(reader, writer, *upstream)
        parts = urlsplit(target)
        try:
            port = parts.port or 80
        except ValueError:  # e.g. http://host:99999/
            return await self._reply(writer, 400, "bad_request")
        if parts.scheme != "http" or not parts.hostname:
            return await self._reply(writer, 400, "bad_request")
        headers = [h for h in rest.split(b"\r\n") if h]
        header_names = [h.split(b":", 1)[0].strip().lower() for h in headers]
        if header_names.count(b"content-length") > 1:
            return await self._reply(writer, 400, "bad_request")  # request smuggling guard
        names = dict(zip(header_names, headers, strict=True))
        if b"transfer-encoding" in names:
            return await self._reply(writer, 501, "bad_request")  # no chunked request bodies
        length = _content_length(names.get(b"content-length"))
        if length is None:
            return await self._reply(writer, 400, "bad_request")
        body = await asyncio.wait_for(reader.readexactly(length), HEAD_TIMEOUT_S) if length else b""
        upstream = await self._open(parts.hostname, port, writer)
        if upstream is None:
            return None
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        kept = [h for h in headers if h.split(b":", 1)[0].strip().lower() not in _HOP_HEADERS]
        request = f"{method} {path} {version}\r\n".encode("latin-1")
        request += b"\r\n".join([*kept, b"Connection: close"]) + b"\r\n\r\n" + body
        up_reader, up_writer = upstream
        up_writer.write(request)
        await up_writer.drain()
        # One request per client connection: nothing more the client sends on it is
        # forwarded (a second request would reach this same, already-vetted upstream).
        # The upstream closes after its answer (Connection: close) and so do we.
        return await self._pipe(None, writer, up_reader, up_writer)

    async def _open(
        self, host: str, port: int, writer: asyncio.StreamWriter
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        if host.lower().rstrip(".") in self.refuse_hosts:
            self.stats.background_refused += 1
            await self._reply(writer, 403, "")
            return None
        verdict, addresses = await resolve(host, port, self.resolver)
        if not verdict.allowed:
            await self._reply(writer, 403, verdict.category)
            return None
        for ip in addresses:
            with contextlib.suppress(OSError, asyncio.TimeoutError):
                return await asyncio.wait_for(self._connect(ip, port), CONNECT_TIMEOUT_S)
        await self._reply(writer, 502, "unreachable")
        return None

    async def _connect(
        self, ip: IP, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        streams = await asyncio.open_connection(str(ip), port)
        self.stats.allowed += 1
        return streams

    async def _reply(self, writer: asyncio.StreamWriter, status: int, why: str) -> None:
        if status == 403 and why:
            self.stats.refused[why] += 1
            log.info("Browser request refused (%s)", why)
        elif why == "bad_request":
            self.stats.bad_requests += 1
        reason = {
            400: "Bad Request",
            403: "Forbidden",
            431: "Too Large",
            501: "Not Implemented",
            502: "Bad Gateway",
            503: "Busy",
        }
        writer.write(f"HTTP/1.1 {status} {reason[status]}\r\nContent-Length: 0\r\n\r\n".encode())
        with contextlib.suppress(ConnectionError):
            await writer.drain()

    async def _pipe(
        self,
        reader: asyncio.StreamReader | None,
        writer: asyncio.StreamWriter,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
    ) -> None:
        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter, out: bool) -> None:
            try:
                while data := await asyncio.wait_for(src.read(65536), IDLE_TIMEOUT_S):
                    if out:
                        self.stats.bytes_out += len(data)
                    else:
                        self.stats.bytes_in += len(data)
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError, asyncio.TimeoutError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    dst.close()

        if reader is None:
            await copy(up_reader, writer, False)
            return
        await asyncio.gather(copy(reader, up_writer, True), copy(up_reader, writer, False))


def _content_length(header: bytes | None) -> int | None:
    """The request body length (0 when absent); None if invalid or too large."""
    if header is None:
        return 0
    value = header.split(b":", 1)[1].strip()
    if not value.isdigit():
        return None
    length = int(value)
    return length if length <= MAX_BODY else None
