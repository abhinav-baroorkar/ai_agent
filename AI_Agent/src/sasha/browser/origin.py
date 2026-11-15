"""Where a URL for `open_url` may come from (review condition B1), as pure functions.

The server decides, not the model: a URL is accepted only if it, or its exact host and
path, appears in a user message of this conversation (typed, or the transcript of a
window voice turn). Tool results and the model's own text never count, so a page that
says "the owner asked you to open ..." can't add a URL. A query string must match the
one the owner wrote (or be absent), so the model can't append conversation text to an
allowed address; the URL actually opened is rebuilt by the server from the owner's own
address (scheme, host, path, the owner's query and fragment), never the model's string.
Searches are built by the server from a `q` argument only. Wake-word turns get only
search and an owner-set site allowlist, never raw URLs.
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
    scheme: str
    host: str
    path: str
    query: str
    fragment: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.host, self.path

    def url(self, query: str | None = None, fragment: str | None = None) -> str:
        """Rebuilt from parts; never the string the model wrote."""
        q = self.query if query is None else query
        f = self.fragment if fragment is None else fragment
        return (
            f"{self.scheme}://{self.host}{self.path}"
            + (f"?{q}" if q else "")
            + (f"#{f}" if f else "")
        )


@dataclass(frozen=True)
class OriginVerdict:
    allowed: bool
    reason: str  # "owner_url", "allowlist" or why it was refused
    url: str | None = None  # what to navigate to (rebuilt by the server), if allowed


def normalise(url: str) -> Address | None:
    """http(s) only; host lowercased (IDNA), default port dropped, empty path = "/".
    None if it is not a web address."""
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
    return Address(parts.scheme, host, parts.path or "/", parts.query, parts.fragment)


def owner_addresses(user_messages: Iterable[str]) -> list[Address]:
    """Every address the owner wrote in this conversation's user messages."""
    found: list[Address] = []
    for message in user_messages:
        for match in _URLISH.finditer(message):
            address = normalise(match.group(0))
            if address is not None and address not in found:
                found.append(address)
    return found


class SearchRefusedError(ValueError):
    """The query can't be searched (empty, or a redirect shortcut like `!w` or `\\`)."""


def search_url(template: str, q: str) -> str:
    """The only way to search: the server builds the URL from the template and the
    model's `q`. A model-written search URL is never accepted (it could carry extra
    parameters or a fragment). Queries starting with `!` or `\\` are refused: some
    engines treat them as redirects to other sites."""
    if template.count("{q}") != 1:
        raise ValueError("search template needs exactly one {q} placeholder")
    query = " ".join(q.split())[:200]
    if not query:
        raise SearchRefusedError("empty search")
    if query.startswith(("!", "\\")):
        raise SearchRefusedError("search shortcuts that redirect are not allowed")
    return template.replace("{q}", quote_plus(query))


def check_open_url(
    url: str,
    user_messages: Sequence[str],
    *,
    wake_turn: bool = False,
    allowlist: Iterable[str] = (),
) -> OriginVerdict:
    """Decide on a model-written URL and return the URL to navigate to, rebuilt by the
    server from the owner's own address: the model's fragment is dropped and any query
    other than the owner's is refused, so nothing the model adds reaches the page."""
    target = normalise(url)
    if target is None:
        return OriginVerdict(False, "not_a_web_address")
    if wake_turn:
        hosts = {a.host for a in (normalise(site) for site in allowlist) if a is not None}
        if target.host in hosts and not target.query:
            # an allowlisted site: its path only (no query, no fragment from the model)
            return OriginVerdict(True, "allowlist", target.url(query="", fragment=""))
        return OriginVerdict(False, "wake_turn_raw_url")
    for owned in owner_addresses(user_messages):
        if owned.key == target.key and target.query in ("", owned.query):
            return OriginVerdict(True, "owner_url", owned.url())
    return OriginVerdict(False, "not_from_owner")
