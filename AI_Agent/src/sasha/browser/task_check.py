"""What the main model may hand the browse helper (design 10.2 with review P2).

The task is the main model's text, and that model has seen device names, states and
the whole conversation. So, before the helper starts:
1. Structural: every word of the task is in the owner's current message, in his message
   just before it, or in a small fixed vocabulary of stop words and task words. A
   device's state, a reminder's text or an earlier code word can only get in if the
   owner has just said it himself.
2. The search leak check (`leaks.check_query`) on the task, against all his messages.
3. At most 300 characters, control and invisible format characters removed.
"""

from __future__ import annotations

import unicodedata

from sasha.browser.leaks import QueryLeakError, check_query
from sasha.said import raw_words, words

MAX_TASK_CHARS = 300
OWN_WORDS = "Use the owner's own words for the lookup"
SEARCH_WORDS = "Search only with words from the task or from pages you have read"
MAX_PAGE_WORDS = 20_000
_TASK_TEXT = """a an the of in on at to for from by with about and or is are was were be
it its this that these those what whats which who whom whose when where why how much many
find look up search check get tell show give me us price prices cost costs weather forecast
today tomorrow now current latest news time times date dates open hours page site website
web result results first second third top best near nearby info information details
summary summarise summarize read say says does do there any some out please can could you
your his her their our"""
TASK_WORDS = frozenset(_TASK_TEXT.split())


def clean(text: str) -> str:
    """Control and invisible format characters out, spaces collapsed."""
    kept = (" " if unicodedata.category(c) in ("Cc", "Cf") else c for c in str(text))
    return " ".join("".join(kept).split())


def outside(text: str, allowed: set[str] | frozenset[str]) -> set[str]:
    """The words of `text` not in `allowed` (a plural of an allowed word is allowed)."""
    return {
        w
        for w in raw_words(text)
        if w not in allowed and not (w.endswith("s") and {w[:-1], w[:-2]} & allowed)
    }


def check_search_words(query: str, task: str, current: str, page_words: set[str]) -> None:
    """The helper's own searches (design 10.2, W2): every word is in the owner's current
    request, the task, page text the helper already read, or the fixed vocabulary. A
    page can't know the owner's earlier words or the home's device names, so it can't
    get them searched. ValueError otherwise."""
    if outside(query, words(current) | words(task) | page_words | TASK_WORDS):
        raise ValueError(SEARCH_WORDS)


def check_task(task: str, owner_messages: tuple[str, ...]) -> str:
    """The cleaned task, or ValueError with a message the main model can act on."""
    text = clean(task)
    if not text:
        raise ValueError("Give the lookup as a short task in the owner's words")
    if len(text) > MAX_TASK_CHARS:
        raise ValueError(f"Keep the lookup under {MAX_TASK_CHARS} characters")
    current = owner_messages[-1] if owner_messages else ""
    previous = owner_messages[-2] if len(owner_messages) > 1 else ""
    if outside(text, words(current) | words(previous) | TASK_WORDS):
        raise ValueError(OWN_WORDS)
    try:
        check_query(text, owner_messages[:-1], current)
    except QueryLeakError as e:
        message = str(e).replace("Searches", "Lookups").replace("search again", "try again")
        raise ValueError(message) from None
    return text
