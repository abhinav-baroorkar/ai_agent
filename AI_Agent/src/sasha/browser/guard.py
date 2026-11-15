"""The address guard: the browser may reach public internet addresses only.

Refused: loopback, private, link-local (including the 169.254.169.254 metadata
address), shared/CGNAT, benchmarking, reserved, multicast and unspecified space, in
IPv4 and IPv6, including IPv4 hidden inside IPv6 (mapped, 6to4, Teredo, NAT64). The rule
is "allow only `ip.is_global`" after unwrapping, so new special ranges are refused by
default. Host strings are parsed the way browsers do, so `0`, `2130706433`,
`0x7f.1` and `017700000001` are all 127.0.0.1.

Pure functions, no I/O except `resolve`, which takes the resolver as an argument.
Refusals carry a category only (for counters), never the host.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

IP = ipaddress.IPv4Address | ipaddress.IPv6Address
ALLOWED_PORTS = frozenset({80, 443})
NAT64 = ipaddress.ip_network("64:ff9b::/96")
NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
METADATA = ipaddress.ip_address("169.254.169.254")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    category: str  # "ok" or why: loopback, private, link_local, metadata, ...


OK = Verdict(True, "ok")


def parse_ip(host: str) -> IP | None:
    """The address a host string names, or None if it is a name to resolve.

    IPv4 follows the WHATWG URL rules browsers use: 1-4 parts, each decimal, `0x` hex or
    leading-0 octal (`127.1`, `0x7f000001`, `2130706433`, `0`)."""
    text = host.strip().rstrip(".").lower()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if ":" in text:
        try:
            return ipaddress.IPv6Address(text.split("%", 1)[0])
        except ValueError:
            return None
    parts = text.split(".")
    if not 1 <= len(parts) <= 4 or not all(_is_number(p) for p in parts):
        return None
    values = [_number(p) for p in parts]
    *head, last = values
    if any(v > 255 for v in head) or last >= 256 ** (5 - len(values)):
        return None
    value = 0
    for v in head:
        value = value * 256 + v
    value = value * 256 ** (5 - len(values)) + last
    return ipaddress.IPv4Address(value) if value < 2**32 else None


def _is_number(part: str) -> bool:
    if not part:
        return False
    if part.startswith("0x"):
        return all(c in "0123456789abcdef" for c in part[2:])
    if len(part) > 1 and part.startswith("0"):
        return all(c in "01234567" for c in part[1:])
    return part.isdigit()


def _number(part: str) -> int:
    if part.startswith("0x"):
        return int(part[2:] or "0", 16)
    if len(part) > 1 and part.startswith("0"):
        return int(part[1:], 8)
    return int(part)


def embedded_ipv4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """IPv4 addresses an IPv6 address carries (mapped, 6to4, Teredo, NAT64)."""
    found: list[ipaddress.IPv4Address] = []
    if ip.ipv4_mapped is not None:
        found.append(ip.ipv4_mapped)
    if ip.sixtofour is not None:
        found.append(ip.sixtofour)
    if ip.teredo is not None:
        found.extend(ip.teredo)
    if ip in NAT64:
        found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return found


def category(ip: IP) -> str:
    """Why an address is refused ("ok" if it is allowed)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in NAT64_LOCAL:
            return "nat64"
        for inner in embedded_ipv4(ip):
            if (why := category(inner)) != "ok":
                return why
    if ip == METADATA:
        return "metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_private:
        return "private"
    if not ip.is_global:
        return "reserved"  # shared/CGNAT, benchmarking, 240/4, 100::/64, ...
    return "ok"


def check_ip(ip: IP) -> Verdict:
    why = category(ip)
    return OK if why == "ok" else Verdict(False, why)


def check_port(port: int) -> Verdict:
    return OK if port in ALLOWED_PORTS else Verdict(False, "port")


def check_addresses(addresses: Iterable[IP]) -> Verdict:
    """Every address a name resolves to must be allowed (one bad one refuses all)."""
    seen = False
    for ip in addresses:
        seen = True
        verdict = check_ip(ip)
        if not verdict.allowed:
            return verdict
    return OK if seen else Verdict(False, "unresolvable")


Resolver = Callable[[str, int], Awaitable[list[IP]]]


async def resolve(host: str, port: int, resolver: Resolver) -> tuple[Verdict, list[IP]]:
    """Check a host (a literal or a name) for one connection. Returns the addresses to
    connect to, already vetted, so nothing can change between the check and the connect."""
    if not (verdict := check_port(port)).allowed:
        return verdict, []
    literal = parse_ip(host)
    if literal is not None:
        return check_ip(literal), [literal]
    if all(_is_number(p) or p.isdigit() for p in host.strip().rstrip(".").split(".")):
        return Verdict(False, "invalid_ip"), []  # e.g. 256.1.1.1: never a name to resolve
    if not host or len(host) > 253 or host.endswith((".localhost", ".local", ".internal")):
        return Verdict(False, "local_name"), []
    if host.lower().rstrip(".") in ("localhost", "invalid") or host.lower().endswith(".invalid"):
        return Verdict(False, "local_name"), []
    try:
        addresses = await resolver(host, port)
    except OSError:
        return Verdict(False, "unresolvable"), []
    verdict = check_addresses(addresses)
    return verdict, addresses if verdict.allowed else []


async def system_resolver(host: str, port: int) -> list[IP]:
    """getaddrinfo through the event loop, for TCP."""
    import asyncio

    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    out: list[IP] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        if ip not in out:
            out.append(ip)
    return out
