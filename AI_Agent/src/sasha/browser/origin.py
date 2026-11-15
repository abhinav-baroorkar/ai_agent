"""Where a URL for `open_url` may come from (review condition B1), as pure functions.

The server decides, not the model: a URL is accepted only if it, or its exact host and
path, appears in a user message of this conversation (typed, or the transcript of a
window voice turn). Tool results and the model's own text never count, so a page that
says "the owner asked you to open ..." can't add a URL. A query string must match the
one the owner wrote (or be absent), so the model can't append conversation text to an
allowed address. Wake-word turns get only the search template and an owner-set site
allowlist, never raw URLs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import quote_plus, urlsplit

# Things in a message that look like an address: with a scheme, or a bare host.tld/path.
_URLISH = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,63}(?::\d{1,5})?(?:/[^\s<>\"']*)?", re.I
)
_TRAILING = ".,;:!?)]}'\""


@dataclass(frozen=True)
class Address:
    host: str
    path: str
    query: str

    @property
    def key(self) -> tuple[str, str]:
        return self.host, self.path


@dataclass(frozen=True)
class OriginVerdict:
    allowed: bool
    reason: str  # "owner_url", "search", "allowlist" or why it was refused


def normalise(url: str) -> Address | None:
    """http(s) only; host lowercased (IDNA), `www.` kept, default port dropped, empty
    path = "/", fragment dropped. None if it is not a web address."""
    text = url.strip().rstrip(_TRAILING)
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        port = parts.port
    except (ValueError, UnicodeError):
        return None
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        return None
    if port not in (None, 80, 443):
        host = f"{host}:{port}"
    return Address(host=host, path=parts.path or "/", query=parts.query)


def owner_addresses(user_messages: Iterable[str]) -> list[Address]:
    """Every address the owner wrote in this conversation's user messages."""
    found: list[Address] = []
    for message in user_messages:
        for match in _URLISH.finditer(message):
            address = normalise(match.group(0))
            if address is not None and address not in found:
                found.append(address)
    return found


def search_url(template: str, query: str) -> str:
    """The search template with `{q}` replaced by the URL-encoded query."""
    if "{q}" not in template:
        raise ValueError("search template needs a {q} placeholder")
    return template.replace("{q}", quote_plus(query[:200]))


def is_search(url: str, template: str) -> bool:
    target, base = normalise(url), normalise(template.replace("{q}", "x"))
    return target is not None and base is not None and target.key == base.key


def check_open_url(
    url: str,
    user_messages: Sequence[str],
    *,
    wake_turn: bool = False,
    search_template: str | None = None,
    allowlist: Iterable[str] = (),
) -> OriginVerdict:
    target = normalise(url)
    if target is None:
        return OriginVerdict(False, "not_a_web_address")
    if search_template and is_search(url, search_template):
        return OriginVerdict(True, "search")
    if wake_turn:
        hosts = {a.host for a in (normalise(site) for site in allowlist) if a is not None}
        if target.host in hosts:
            return OriginVerdict(True, "allowlist")
        return OriginVerdict(False, "wake_turn_raw_url")
    for owned in owner_addresses(user_messages):
        if owned.key == target.key and target.query in ("", owned.query):
            return OriginVerdict(True, "owner_url")
    return OriginVerdict(False, "not_from_owner")
