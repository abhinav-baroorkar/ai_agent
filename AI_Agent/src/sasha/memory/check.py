"""What the server checks before a note is kept (design §4; the review's M3, M4, M7).

- M4: the owner's message asks to remember (or to forget), with a broad phrase list;
  a question about notes ("what do you remember about my bus?") asks for neither, nor
  does the owner's own memory ("I remember the bus was late", S4).
- M3, all or nothing: every content word of the note is in the owner's own words of
  this turn, after the request's own words are taken out ("don't forget", R5): stop
  words and inflections aside; notes are kept first person, as said, though "the
  owner" for the owner's "I" is still accepted. Every number is one the owner said,
  with the same value, and the note keeps exactly the owner's negations ("not",
  "never", "n't"): it can't drop or add one (R5). "Remember that" with nothing of its
  own is refused, so a note can't be filled from a reply or a page.
  Known limit: words are a bag, so "my brother owes me 50" could be reordered; the
  server shows the stored text back ("Noted: ...", S3) so the owner sees it.
- M7: passwords, PINs, codes, combinations, card or account numbers, long digit runs
  (also spoken: "four eight two one"), key-like strings, a wifi or network password,
  and an email address next to "password" are never stored. `mask_all` takes the
  secret and every number out of the owner's messages before they stay in the history.
The words come from `profile.memory` (English now).
"""

from __future__ import annotations

import re
from collections import Counter

from sasha.config_memory import MemoryConfig
from sasha.config_timers import TimeCues
from sasha.server.timers.said import tokens

_NUMBERS = TimeCues().number_values  # the timers' number words: "eight" is 8
_NUMBER_WORD = re.compile(
    r"\b(?:" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r")\b", re.I
)
_DIGITS = re.compile(r"\d+")
_ASKED_YOU = (("asked", "you", "to"), ("told", "you", "to"))


def _norm(text: str) -> str:
    return " ".join(tokens(text))


def _phrase_at(words: list[str], phrase: list[str], at: int) -> bool:
    return words[at : at + len(phrase)] == phrase


def _requests(words: list[str], phrases: list[str], cfg: MemoryConfig) -> list[tuple[int, int]]:
    """(start, length) of each request phrase in `words`, skipping the owner's own
    memory: "I remember...", "...what I asked you to remember" (S4)."""
    found = []
    for phrase in map(tokens, phrases):
        for at in range(len(words) - len(phrase) + 1):
            if not _phrase_at(words, phrase, at):
                continue
            if phrase == ["remember"]:
                before = words[max(0, at - 3) : at]
                if (before and before[-1] in cfg.self_subjects) or tuple(before) in _ASKED_YOU:
                    continue
            found.append((at, len(phrase)))
    return found


def is_question(text: str, cfg: MemoryConfig) -> bool:
    words = tokens(text)
    return text.strip().endswith("?") and bool(words) and words[0] in cfg.question_starts


def asks_to_remember(text: str, cfg: MemoryConfig) -> bool:
    return not is_question(text, cfg) and bool(_requests(tokens(text), cfg.remember_phrases, cfg))


def asks_to_forget(text: str, cfg: MemoryConfig) -> bool:
    """ "Forget that note" asks; "don't forget I'm allergic" asks to remember instead."""
    if is_question(text, cfg) or asks_to_remember(text, cfg):
        return False
    return bool(_requests(tokens(text), cfg.forget_phrases, cfg))


def owner_pair(
    messages: tuple[str, ...] | list[str], asked: bool, since_s: float | None, cfg: MemoryConfig
) -> list[str]:
    """This turn's owner words: the current message, plus the previous one when the
    reply before asked and that one was a remember request (the asked-back pair, as in
    the timers' S1). The tools and the turn's truth check read the same pair (R2)."""
    fresh = since_s is None or since_s <= cfg.answer_window_s
    if len(messages) > 1 and asked and fresh and asks_to_remember(messages[-2], cfg):
        return [messages[-2], messages[-1]]
    return [messages[-1]] if messages else []


def says_everything(text: str, cfg: MemoryConfig) -> bool:
    return bool(set(tokens(text)) & set(cfg.everything_words))


def says_yes(text: str, cfg: MemoryConfig) -> bool:
    """A plain yes (R1): every word is a yes word or a courtesy, and at least one is a
    yes. "Yes, but only the last one", "ok wait no" and "sure, actually don't" aren't."""
    words = tokens(text)
    yes = {w for y in cfg.yes_words for w in tokens(y)}
    return bool(words) and set(words) <= yes | set(cfg.courtesy_words) and bool(set(words) & yes)


def stem(word: str) -> str:
    """One crude English stemmer: "takes"/"taking"/"taken" all give "tak"."""
    word = word.lower().strip("'")
    if word.endswith("'s"):
        word = word[:-2]
    for suffix in ("ing", "ies", "ied", "es", "ed", "en", "s", "e", "y"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _number(word: str) -> int | None:
    if word.startswith(":") and word[1:].isdigit():
        return int(word[1:])
    if word.isdigit():
        return int(word)
    m = re.fullmatch(r"(\d+)(?:st|nd|rd|th)", word)
    if m:
        return int(m.group(1))
    return _NUMBERS.get(word)


def _negation(word: str, cfg: MemoryConfig) -> bool:
    return word in cfg.negation_words or word.endswith("n't")


def content_words(text: str, cfg: MemoryConfig) -> list[str]:
    """The words that carry meaning: not stop words, not numbers (those are matched by
    value), not the owner rewrites, not negations (those are counted, R5)."""
    skip = set(cfg.stop_words) | set(cfg.owner_words) | set(cfg.self_words)
    return [
        w for w in tokens(text) if w not in skip and _number(w) is None and not _negation(w, cfg)
    ]


def owner_words(owner_text: str, cfg: MemoryConfig) -> list[str]:
    """The owner's words with the request phrases taken out (R5): "Don't forget I take
    the pill at 8" leaves "I take the pill at 8"."""
    out: list[str] = []
    for line in owner_text.splitlines() or [owner_text]:
        words = tokens(line)
        drop: set[int] = set()
        for at, length in _requests(words, cfg.remember_phrases + cfg.forget_phrases, cfg):
            drop |= set(range(at, at + length))
        out += [w for i, w in enumerate(words) if i not in drop]
    return out


def phrase_words(cfg: MemoryConfig) -> set[str]:
    return {w for p in cfg.remember_phrases + cfg.forget_phrases for w in tokens(p)}


def has_content(owner_text: str, cfg: MemoryConfig) -> bool:
    """Anything to remember beyond the request itself? ("Remember that." has none.)"""
    words = [w for w in owner_words(owner_text, cfg) if w not in cfg.stop_words]
    return any(w not in {"that", "this", "it", "please", "ok", "okay"} for w in words)


def unsaid_words(note: str, owner_text: str, cfg: MemoryConfig) -> list[str]:
    """M3: the note's content words the owner didn't say, and any negation it drops or
    adds (empty: the note may be kept)."""
    said_words = owner_words(owner_text, cfg)
    said = {stem(w) for w in said_words}
    missing = [w for w in content_words(note, cfg) if stem(w) not in said]
    owner = set(cfg.owner_words)
    if owner & set(tokens(note)) and not (set(cfg.self_words) | owner) & set(said_words):
        missing.append("owner")  # "the owner..." only for the owner's "I", "my", "me"
    negs = Counter("not" for w in tokens(note) if _negation(w, cfg))
    if negs != Counter("not" for w in said_words if _negation(w, cfg)):
        missing.append("not")  # R5: "I'm not allergic" can't become "allergic"
    return missing


def unsaid_numbers(note: str, owner_text: str) -> list[int]:
    said = {n for n in map(_number, tokens(owner_text)) if n is not None}
    return [n for n in map(_number, tokens(note)) if n is not None and n not in said]


def _numbers(text: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in _DIGITS.finditer(text)]
    return spans + [(m.start(), m.end()) for m in _NUMBER_WORD.finditer(text)]


def _secret_spans(text: str, cfg: MemoryConfig) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    lower = text.lower()
    for word in cfg.secret_words:
        for m in re.finditer(rf"\b{re.escape(word)}\b", lower):
            spans.append((m.start(), len(text)))  # the word and what follows it
    for pattern in cfg.secret_patterns:
        spans += [(m.start(), m.end()) for m in re.finditer(pattern, text, re.IGNORECASE)]
    if "password" in lower:
        spans += [(m.start(), m.end()) for m in re.finditer(cfg.email_pattern, text)]
    # S1: "the code for the gate is 4821": a code word anywhere, with any number.
    if any(re.search(rf"\b{re.escape(w)}\b", lower) for w in cfg.code_words):
        spans += _numbers(text)
    return spans


def has_secret(text: str, cfg: MemoryConfig) -> bool:
    return bool(_secret_spans(text, cfg))


def _masked(text: str, spans: list[tuple[int, int]], cfg: MemoryConfig) -> str:
    out, at = [], 0
    for start, end in sorted(spans):
        if end <= at:
            continue
        out.append(text[at : max(at, start)])
        out.append(cfg.mask)
        at = end
    out.append(text[at:])
    return re.sub(rf"(?:{re.escape(cfg.mask)}\s*)+", f"{cfg.mask} ", "".join(out)).strip()


def mask(text: str, cfg: MemoryConfig) -> str:
    """The text with each secret (and what follows a secret word) replaced."""
    spans = _secret_spans(text, cfg)
    return _masked(text, spans, cfg) if spans else text


def mask_all(text: str, cfg: MemoryConfig) -> str:
    """For a refused secret (R2, R3): the secrets and every number taken out."""
    spans = _secret_spans(text, cfg) + _numbers(text)
    return _masked(text, spans, cfg) if spans else text


def has_number(text: str) -> bool:
    return bool(_numbers(text))
