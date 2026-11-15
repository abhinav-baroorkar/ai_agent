"""The filtering proxy with a fake resolver and a fake upstream (no network)."""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any

from sasha.browser.proxy import FilteringProxy

PUBLIC = "93.184.216.34"


def make_proxy(table: dict[str, list[str]]) -> tuple[FilteringProxy, list[str]]:
    async def resolver(host: str, port: int) -> list[Any]:
        if host not in table:
            raise OSError("no such name")
        return [ipaddress.ip_address(a) for a in table[host]]

    proxy = FilteringProxy(resolver)
    connected: list[str] = []

    async def fake_connect(ip: Any, port: int) -> Any:
        """An in-memory upstream that echoes what it gets (instead of the internet)."""
        connected.append(f"{ip}:{port}")
        server = await asyncio.start_server(_echo, "127.0.0.1", 0)
        local = server.sockets[0].getsockname()[1]
        proxy.stats.allowed += 1
        return await asyncio.open_connection("127.0.0.1", local)

    proxy._connect = fake_connect  # type: ignore[method-assign]
    return proxy, connected


async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    data = await reader.read(65536)
    writer.write(b"ECHO:" + data)
    await writer.drain()
    writer.close()


async def ask(port: int, request: bytes, then: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    if then:
        head = await reader.readuntil(b"\r\n\r\n")
        writer.write(then)
        await writer.drain()
        body = await asyncio.wait_for(reader.read(65536), 2)
        writer.close()
        return head + body
    out = await asyncio.wait_for(reader.read(65536), 2)
    writer.close()
    return out


def run(
    table: dict[str, list[str]], *requests: tuple[bytes, bytes]
) -> tuple[list[bytes], FilteringProxy, list[str]]:
    async def main() -> tuple[list[bytes], FilteringProxy, list[str]]:
        proxy, connected = make_proxy(table)
        port = await proxy.start()
        try:
            out = [await ask(port, req, then) for req, then in requests]
        finally:
            await proxy.stop()
        return out, proxy, connected

    return asyncio.run(main())


def test_connect_to_refused_addresses_gets_403() -> None:
    table = {"router.example": ["192.168.1.1"], "mixed.example": [PUBLIC, "127.0.0.1"]}
    targets = [
        b"127.0.0.1:8765",
        b"127.0.0.1:443",
        b"[::1]:443",
        b"10.0.0.1:443",
        b"169.254.169.254:80",
        b"2130706433:443",
        b"router.example:443",
        b"mixed.example:443",
        b"localhost:443",
        b"test.invalid:443",
        b"public.example:22",
    ]
    reqs = [(b"CONNECT " + t + b" HTTP/1.1\r\nHost: x\r\n\r\n", b"") for t in targets]
    out, proxy, connected = run({**table, "public.example": [PUBLIC]}, *reqs)
    assert all(o.startswith(b"HTTP/1.1 403") for o in out), out
    assert connected == []
    # the port is checked first, so 127.0.0.1:8765 counts as "port"
    assert proxy.stats.refused == {
        "loopback": 4,
        "private": 2,
        "metadata": 1,
        "local_name": 2,
        "port": 2,
    }


def test_connect_to_a_public_name_is_tunnelled_to_the_vetted_address() -> None:
    out, proxy, connected = run(
        {"public.example": [PUBLIC]},
        (b"CONNECT public.example:443 HTTP/1.1\r\nHost: public.example:443\r\n\r\n", b"hello"),
    )
    assert out[0].startswith(b"HTTP/1.1 200") and out[0].endswith(b"ECHO:hello")
    assert connected == [f"{PUBLIC}:443"] and proxy.stats.allowed == 1


def test_plain_http_is_rewritten_and_hop_headers_dropped() -> None:
    req = (
        b"GET http://public.example/a?b=1 HTTP/1.1\r\nHost: public.example\r\n"
        b"Proxy-Connection: keep-alive\r\nProxy-Authorization: x\r\nAccept: */*\r\n\r\n"
    )
    out, _, connected = run({"public.example": [PUBLIC]}, (req, b""))
    echoed = out[0]
    assert echoed.startswith(b"ECHO:GET /a?b=1 HTTP/1.1\r\n")
    assert b"Proxy-" not in echoed and b"Connection: close" in echoed and b"Accept: */*" in echoed
    assert connected == [f"{PUBLIC}:80"]


def test_plain_http_to_loopback_is_refused() -> None:
    req = b"GET http://127.0.0.1:8765/api/health HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n"
    out, _, connected = run({}, (req, b""))
    assert out[0].startswith(b"HTTP/1.1 403") and connected == []


def test_bad_requests_get_400() -> None:
    out, proxy, _ = run({}, (b"GET /relative HTTP/1.1\r\n\r\n", b""), (b"NONSENSE\r\n\r\n", b""))
    assert all(o.startswith(b"HTTP/1.1 400") for o in out)
    assert proxy.stats.bad_requests == 2


def test_the_proxy_logs_no_hosts(caplog: Any) -> None:
    import logging

    with caplog.at_level(logging.DEBUG):
        run(
            {"secret-host.example": ["10.9.9.9"]},
            (b"CONNECT secret-host.example:443 HTTP/1.1\r\n\r\n", b""),
        )
    assert "secret-host" not in caplog.text and "10.9.9.9" not in caplog.text
