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
from sasha.browser.task_check import OWN_WORDS, TASK_WORDS, outside
from sasha.browser.tools import NOTE
from sasha.config_wikipedia import WikipediaConfig
from sasha.conversation import current_turn
from sasha.said import words
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


def _refused(message: str, label: str) -> ToolError:
    err = ToolError(message)
    err.asks = label
    return err


class WikipediaLookup:
    def __init__(
        self,
        language: str,
        config: WikipediaConfig,
        fetch: Fetch = http_get,
        owner_words: bool = False,
    ) -> None:
        """`owner_words`: the main assistant's lookups (beside `browse`). Its context
        holds memory notes, device names and earlier chat, so every word of the topic
        must come from the owner's current or previous message, as a `browse` task's
        (task_check, design 10.2; review F1). A bot with the direct browser is held to
        the leak check only, as its own `search` is."""
        if not _LANGUAGE.match(language):
            raise ValueError(f"not a Wikipedia language code: {language!r}")
        self.host = f"{language}.wikipedia.org"
        self.config = config
        self.fetch = fetch
        self.owner_words = owner_words

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
        if ctx.input_mode == "wake":  # its own label: the reply says the real reason
            raise _refused("Wikipedia isn't available for wake-word requests", "wake_turn")
        if not topic:
            raise ToolError("Say what to look up on Wikipedia")
        owner = ctx.user_messages
        current = owner[-1] if owner else ""
        if self.owner_words:
            previous = owner[-2] if len(owner) > 1 else ""
            if outside(topic, words(current) | words(previous) | TASK_WORDS):
                raise _refused(OWN_WORDS, "not_owner_words")
        try:  # the leak check, with the turn's earlier queries (searches too, W2)
            check_query(topic, owner[:-1], current, ctx.queries)
        except QueryLeakError as e:
            raise _refused(str(e), "not_private") from None
        ctx.queries.append(topic)
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
        if not isinstance(key, str) or key in ("", ".", "..") or len(key) > 300:
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
