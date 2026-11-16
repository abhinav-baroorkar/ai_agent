"""After a take-over, keep only the search engine's cookies and site data (§9.0 C3).

Whatever the owner did in the visible window would otherwise become the assistant's
state: a sign-in made despite the warning, tracking or consent state, a site's
history. The only state the take-over is for is the engine's passed-check cookie. So
at the first headless start after it, before any navigation: every cookie is read
(`Storage.getCookies`), all are cleared, the engine's are put back, and site data is
cleared for every other host that had a cookie. Browser-level DevTools calls only.
"""

from __future__ import annotations

import contextlib
from typing import Any

from sasha.browser.cdp import CDPError
from sasha.browser.redirects import site_of


async def keep_only_site_data(chrome: Any, site: str) -> int:
    """Returns how many other sites were forgotten (counts only, nothing logged)."""
    pipe = chrome.pipe
    cookies = (await pipe.call("Storage.getCookies")).get("cookies", [])
    keep = [c for c in cookies if site_of(str(c.get("domain", "")).lstrip(".")) == site]
    others = {
        str(c.get("domain", "")).lstrip(".") for c in cookies if c not in keep and c.get("domain")
    }
    await pipe.call("Storage.clearCookies")
    if keep:
        await pipe.call("Storage.setCookies", {"cookies": [_settable(c) for c in keep]})
    for host in sorted(others):
        for scheme in ("https", "http"):
            with contextlib.suppress(CDPError):
                await pipe.call(
                    "Storage.clearDataForOrigin",
                    {"origin": f"{scheme}://{host}", "storageTypes": "all"},
                )
    return len(others)


def _settable(cookie: dict[str, Any]) -> dict[str, Any]:
    """A cookie as read, in the shape `Storage.setCookies` takes back."""
    fields = ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite", "expires")
    out = {k: cookie[k] for k in fields if k in cookie}
    if out.get("expires", 0) in (-1, 0):
        out.pop("expires", None)  # a session cookie
    return out
