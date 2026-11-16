"""See through a search engine's result-redirect links.

DuckDuckGo's HTML results link to `https://duckduckgo.com/l/?uddg=<real address>&...`.
The model should see, and the browser should open, the real destination, not the
redirect: the tool then navigates straight to it, so the address guard checks the
place the owner will actually land (the proxy checks every hop anyway). Only links on
the search engine's own site are unwrapped, and only to http(s) addresses.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlsplit

# path on the engine's site -> the query parameter that holds the real address
REDIRECTS = {"/l/": "uddg"}


def site_of(host: str) -> str:
    """The last two labels of a host ("html.duckduckgo.com" -> "duckduckgo.com")."""
    labels = host.lower().rstrip(".").split(".")
    return ".".join(labels[-2:])


def unwrap(href: str, base: str, engine_host: str | None) -> str:
    """The real destination of `href` (resolved against `base`), else `href` itself."""
    absolute = urljoin(base, href)
    if not engine_host:
        return absolute
    parts = urlsplit(absolute)
    if site_of(parts.hostname or "") != site_of(engine_host):
        return absolute
    param = REDIRECTS.get(parts.path)
    if param is None:
        return absolute
    values = parse_qs(parts.query).get(param)
    if not values:
        return absolute
    target = values[0]
    if urlsplit(target).scheme not in ("http", "https"):
        return absolute  # never unwrap into javascript:, data:, file: ...
    return target


def short(url: str) -> str:
    """What the model is shown for a link's destination: host and path, no query."""
    parts = urlsplit(url)
    path = parts.path if parts.path not in ("", "/") else ""
    text = f"{parts.hostname or ''}{path}"
    return text if len(text) <= 80 else text[:79] + "…"


# Engines whose results always go through a redirect on their own site (REDIRECTS): for
# them only those links count as results, so a challenge page with a stray off-site link
# (a captcha provider's terms, a help page) doesn't pass as results (review S2).
REDIRECT_ENGINES = frozenset({"duckduckgo.com"})
MIN_RESULTS = 3


def looks_like_results(engine_host: str | None, unwrapped: int, offsite: int) -> bool:
    if engine_host and site_of(engine_host) in REDIRECT_ENGINES:
        return unwrapped >= MIN_RESULTS
    return unwrapped + offsite > 0  # an engine we don't know: any result-shaped link
