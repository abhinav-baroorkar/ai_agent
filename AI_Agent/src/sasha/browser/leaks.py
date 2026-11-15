"""Keep personal identifiers out of search queries (found by the injection set, B7).

A page can ask the model to "search for the user's email" or "the code word they told
you": the query itself then carries private text to the network. The model sometimes
complies, so the server refuses, whatever the page said, a query that contains
- an email address (also full-width or percent-encoded, spaced out, or an email the
  owner wrote earlier spelled with "at" and "dot"),
- a long number (7+ digits: phone, account, card; digits may be spelled as words), or
- an identifier-like token (letters and digits mixed, e.g. `mango-42`, `AB12CD`) that
  the owner wrote in an earlier message of this conversation and not in the request
  being answered, compared with spacing and punctuation removed.
The digit and identifier rules look at all of this turn's searches together, so a value
can't be split across two searches (review W2). Ordinary words are never refused.
This is a denylist and can be got around; B4 (no private connectors next to the
browser) stays the guarantee.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import unquote

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_NUMBER = re.compile(r"\d[\d\s().-]{5,}\d")
_SPACED_AT = re.compile(r"\s*@\s*")
_SPACED_DOT = re.compile(r"(?<=\w)\s*\.\s*(?=\w)")
_SPOKEN = re.compile(r"[\(\[]?\b(at|dot)\b[\)\]]?")
_NUMBER_WORDS = {
    w: str(i)
    for i, w in enumerate(
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    )
} | {"oh": "0"}
_WORD = re.compile(r"[a-z]+")
_CHUNKS = re.compile(r"[a-z]+|\d+")
_TOKEN = re.compile(r"[\w-]+")
REPHRASE = "; search again without the personal detail"


class QueryLeakError(ValueError):
    """The query would carry a personal identifier off the machine."""


def normalise(text: str) -> str:
    """NFKC (full-width @ and digits become plain), percent-decoded once, lower case."""
    return unicodedata.normalize("NFKC", unquote(text)).lower()


def _squeeze(text: str) -> str:
    return re.sub(r"[\W_]+", "", text)


def _identifiers(text: str) -> set[str]:
    found = set()
    for token in _TOKEN.findall(normalise(text)):
        letters = any(c.isalpha() for c in token)
        digits = any(c.isdigit() for c in token)
        if letters and digits and len(_squeeze(token)) >= 4:
            found.add(_squeeze(token))
    return found


def _emails(text: str) -> set[str]:
    return {_squeeze(e) for e in _EMAIL.findall(normalise(text))}


def _digits_from_words(text: str) -> str:
    return _WORD.sub(lambda m: _NUMBER_WORDS.get(m.group(), m.group()), text)


def check_query(
    query: str, earlier: Iterable[str], current: str, before: Iterable[str] = ()
) -> None:
    """Refuse `query` (QueryLeakError). `earlier`: the owner's earlier messages in this
    conversation; `current`: the request being answered; `before`: this turn's searches
    already made."""
    earlier = list(earlier)
    q = normalise(query)
    compact = _SPACED_DOT.sub(".", _SPACED_AT.sub("@", q))
    if _EMAIL.search(compact):
        raise QueryLeakError("Searches can't contain email addresses" + REPHRASE)
    unspoken = _squeeze(_SPOKEN.sub("", q))
    owner_emails = set().union(*(_emails(m) for m in earlier)) - _emails(current)
    if any(e in unspoken for e in owner_emails):
        raise QueryLeakError("Searches can't contain email addresses" + REPHRASE)

    as_digits = _digits_from_words(q)
    if any(sum(c.isdigit() for c in m) >= 7 for m in _LONG_NUMBER.findall(as_digits)):
        raise QueryLeakError("Searches can't contain long numbers" + REPHRASE)
    turn = " ".join(normalise(b) for b in before) + " " + q
    turn_digits = "".join(c for c in _digits_from_words(turn) if c.isdigit())
    current_numbers = {_squeeze(n) for n in _LONG_NUMBER.findall(normalise(current))}
    for message in earlier:
        for number in _LONG_NUMBER.findall(_digits_from_words(normalise(message))):
            digits = "".join(c for c in number if c.isdigit())
            if len(digits) >= 7 and digits not in current_numbers and digits in turn_digits:
                raise QueryLeakError("Searches can't contain long numbers" + REPHRASE)

    earlier_ids = set().union(*(_identifiers(m) for m in earlier)) - _identifiers(current)
    spoken = _digits_from_words(turn)  # "mango four two" -> "mango 4 2"
    squeezed_turn, squeezed_spoken = _squeeze(turn), _squeeze(spoken)
    turn_chunks = set(_CHUNKS.findall(turn)) | set(_CHUNKS.findall(spoken))
    for ident in earlier_ids:
        chunks = _CHUNKS.findall(ident)
        if (
            ident in squeezed_turn
            or ident in squeezed_spoken
            or all(c in turn_chunks for c in chunks)
        ):
            raise QueryLeakError(
                "Searches can't contain codes or identifiers from earlier messages" + REPHRASE
            )
