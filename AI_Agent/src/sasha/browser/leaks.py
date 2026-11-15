"""Keep personal identifiers out of search queries (found by the injection set, B7).

A page can ask the model to "search for the user's email" or "the code word they told
you": the query itself then carries private text to the network. The model sometimes
complies, so the server refuses, whatever the page said, a query that contains
- an email address,
- a long number (7+ digits: phone, account, card), or
- an identifier-like token (letters and digits mixed, e.g. `mango-42`, `AB12CD`) that
  the owner wrote in an earlier message of this conversation and not in the request
  being answered.
Ordinary words are never refused, even if the owner said them before.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_NUMBER = re.compile(r"\d[\d\s().-]{5,}\d")
_TOKEN = re.compile(r"[\w-]+")


class QueryLeakError(ValueError):
    """The query would carry a personal identifier off the machine."""


def _identifiers(text: str) -> set[str]:
    found = set()
    for token in _TOKEN.findall(text.lower()):
        letters = any(c.isalpha() for c in token)
        digits = any(c.isdigit() for c in token)
        if letters and digits and len(token) >= 4:
            found.add(token)
    return found


def check_query(query: str, earlier: Iterable[str], current: str) -> None:
    if _EMAIL.search(query):
        raise QueryLeakError("Searches can't contain email addresses")
    if any(sum(c.isdigit() for c in m) >= 7 for m in _LONG_NUMBER.findall(query)):
        raise QueryLeakError("Searches can't contain long numbers")
    earlier_ids = set().union(*(_identifiers(m) for m in earlier)) - _identifiers(current)
    if _identifiers(query) & earlier_ids:
        raise QueryLeakError("Searches can't contain codes or identifiers from earlier messages")
