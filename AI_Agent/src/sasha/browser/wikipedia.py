"""Look a topic up on Wikipedia through Wikimedia's public API (owner's decision,
2026-11-16: no keyed search service, nothing tied to him; Wikipedia needs no key, no
account and shows no person check).

Behind the browser connector's rules: read-only, every result is outside text
(`taints`, wrapped as untrusted), the topic passes the same leak check as `search`
(leaks.check_query), and a failed lookup is said by the server (browse_truth.py).
Every address is built here, on the wiki's own host: the topic goes into the search's
query, and the page key the search returns is escaped into the summary's path and the
page address. No address from a response is used as it is.

Two GETs, one after the other (API etiquette: serial, JSON, gzip):
1. the wiki's own REST search (`/w/rest.php/v1/search/page?q=…&limit=1`) for the page;
2. its summary (`/api/rest_v1/page/summary/<key>`): title and a plain-text extract.
   If that fails, the search's own short description and excerpt stand in.
The result: the title, a short extract and the page's address, all untrusted data.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from urllib.parse import quote, urlencode

from sasha.browser.leaks import QueryLeakError, check_query
from sasha.browser.reader import clean
from sasha.browser.tools import NOTE
from sasha.config_wikipedia import WikipediaConfig
from sasha.conversation import current_turn
from sasha.tools import Tool, ToolError

WIKI_TOOL = "wikipedia"
MAX_TOPIC = 200
TITLE_CHARS = 200
_LANGUAGE = re.compile(r"^[a-z]{2,3}(-[a-z]{2,8})?$")
_TAGS = re.compile(r"<[^>]*>")
FAILED = "Wikipedia couldn't be reached just now."
NOT_SET_UP = (
    "Wikipedia lookups aren't set up: the owner hasn't chosen how this assistant names "
    "itself to Wikipedia (profile setting wikipedia.user_agent)."
)
_CONTACT = re.compile(r"https?://[^\s()]+\.[^\s()]+|[^\s()@]+@[^\s()@]+\.[^\s()@]+")
NOTHING = "Nothing on Wikipedia matched that."

# GET url with headers and a timeout -> (HTTP status, decoded JSON or None)
Fetch = Callable[[str, dict[str, str], float], tuple[int, Any]]


def has_contact(user_agent: str) -> bool:
    """Wikimedia's policy: an agent names a way to reach its maker (a URL or an email)."""
    return bool(_CONTACT.search(user_agent or ""))


def http_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, Any]:
    """One GET: no redirects followed (an address is only ever one we built), no proxy
    or netrc from the environment, gzip accepted (httpx's default)."""
    import httpx

    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        resp = client.get(url, headers=headers)
    if resp.status_code != 200:
        return resp.status_code, None
    try:
        return 200, resp.json()
    except ValueError:
        return 200, None


class WikipediaLookup:
    def __init__(self, language: str, config: WikipediaConfig, fetch: Fetch = http_get) -> None:
        if not _LANGUAGE.match(language):
            raise ValueError(f"not a Wikipedia language code: {language!r}")
        self.host = f"{language}.wikipedia.org"
        self.config = config
        self.fetch = fetch

    def _get(self, url: str) -> Any:
        headers = {"User-Agent": self.config.user_agent, "Accept": "application/json"}
        try:
            status, data = self.fetch(url, headers, self.config.timeout_s)
        except Exception:  # timeouts, DNS, refused: never the details to the model
            err = ToolError(FAILED)
            err.asks = "failed"
            raise err from None
        return data if status == 200 and isinstance(data, dict) else None

    def lookup(self, topic: str) -> dict[str, Any]:
        if not has_contact(self.config.user_agent):  # nothing is sent (the owner's call)
            err = ToolError(NOT_SET_UP)
            err.asks = "not_set_up"
            raise err
        topic = " ".join(str(topic).split())[:MAX_TOPIC]
        ctx = current_turn.get()
        if ctx is None:
            raise ToolError("Wikipedia can only be used in a conversation")
        if ctx.input_mode == "wake":
            raise ToolError("Wikipedia isn't available for wake-word requests")
        if not topic:
            raise ToolError("Say what to look up on Wikipedia")
        owner = ctx.user_messages
        try:  # the same rule as a web search: nothing private leaves in the topic
            check_query(topic, owner[:-1], owner[-1] if owner else "")
        except QueryLeakError as e:
            raise ToolError(str(e)) from None
        found = self._get(
            f"https://{self.host}/w/rest.php/v1/search/page?" + urlencode({"q": topic, "limit": 1})
        )
        if found is None:
            err = ToolError(FAILED)
            err.asks = "failed"
            raise err
        pages = found.get("pages")
        page = pages[0] if isinstance(pages, list) and pages else None
        key = page.get("key") if isinstance(page, dict) else None
        if not isinstance(key, str) or not key or len(key) > 300:
            err = ToolError(NOTHING)
            err.asks = "nothing_found"
            raise err
        assert isinstance(page, dict)
        path = quote(key, safe="")
        summary = self._get(f"https://{self.host}/api/rest_v1/page/summary/{path}")
        title = (summary or {}).get("title") or page.get("title") or key
        extract = (summary or {}).get("extract")
        if not isinstance(extract, str) or not extract.strip():  # the search stands in
            parts = [page.get("description"), _TAGS.sub("", str(page.get("excerpt") or ""))]
            extract = " ".join(str(p) for p in parts if p)
        return {
            "site": self.host,
            "title": clean(str(title), TITLE_CHARS),
            "extract": clean(extract, self.config.extract_chars),
            "url": f"https://{self.host}/wiki/{quote(key, safe=':_()')}",  # built here
            "note": NOTE,
        }


def wikipedia_tool(lookup: WikipediaLookup) -> Tool:
    tool = Tool(
        WIKI_TOOL,
        "Look a topic up on Wikipedia: its title, a short summary and the page's address. "
        "Use it when the owner says 'on Wikipedia', or for a plain fact about a person, "
        "place, thing or event.",
        {
            "type": "object",
            "properties": {"topic": {"type": "string", "description": "What to look up"}},
            "required": ["topic"],
        },
        lookup.lookup,
    )
    return replace(tool, read_only=True, taints=True)  # outside text, never a change
