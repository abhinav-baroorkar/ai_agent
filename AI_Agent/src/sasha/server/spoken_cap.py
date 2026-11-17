"""A spoken answer that came from a web lookup (review P8).

On `speak=1` sockets (the Home window: what is shown is what is spoken), once a
`browse` call has returned in the turn, the rest of the answer is released whole
sentences at a time up to `SPOKEN_LOOKUP_CHARS`, the same cap as the lookup's own
answer; what doesn't fit is neither shown nor spoken. Then, unless the answer already
named it, the server adds "From <site>." with the first site the tabs really loaded
(the server's own record, P4), so the owner hears where it came from. The site is a
hostname from the server's record ("www." dropped), never model text. A Wikipedia
lookup (`wikipedia`) gets the same, said as "From Wikipedia." (review of
feat/wikipedia-lookup: from the server, not left to the prompt).
"""

from __future__ import annotations

import re
from collections.abc import Callable

SPOKEN_LOOKUP_CHARS = 500
_SENTENCE_END = re.compile(r"[.!?]+(\s+|$)")


def spoken_site(site: str) -> str:
    """How a site is said: Wikipedia by name (any language's host), else the host."""
    site = site.lower().removeprefix("www.")
    return "Wikipedia" if site == "wikipedia.org" or site.endswith(".wikipedia.org") else site


def _named(site: str, said: str) -> bool:
    label = site.split(".", 1)[0]
    text = said.lower()
    name = spoken_site(site).lower()
    return site in text or name in text or (len(label) >= 3 and label in text)


class LookupCap:
    def __init__(self, release: Callable[[str], None], cap: int = SPOKEN_LOOKUP_CHARS) -> None:
        self.release = release  # shows and speaks a piece (the socket's own path)
        self.cap = cap
        self.held = ""
        self.used = 0
        self.full = False
        self.said = ""

    def _out(self, text: str) -> None:
        self.used += len(text)
        self.said += text
        self.release(text)

    def feed(self, piece: str) -> None:
        if self.full:
            return
        self.held += piece
        while (end := _SENTENCE_END.search(self.held)) is not None:
            sentence, self.held = self.held[: end.end()], self.held[end.end() :]
            if self.used + len(sentence) > self.cap:
                self.full, self.held = True, ""
                return
            self._out(sentence)

    def finish(self, sites: list[str], before: str = "") -> None:
        """The last part if it fits (or, if nothing was said yet, cut at a word), then
        the site. `before`: what the turn showed before the lookup."""
        rest = self.held.strip()
        if rest and not self.full:
            if self.used + len(rest) <= self.cap:
                self._out(self.held)
            elif not self.used:
                self._out(rest[: self.cap].rsplit(" ", 1)[0] + "…")
        self.held = ""
        site = sites[0].lower().removeprefix("www.") if sites else ""
        if site and not _named(site, before + self.said):
            space = "" if not self.said or self.said.endswith((" ", "\n")) else " "
            self.release(f"{space}From {spoken_site(site)}.")
