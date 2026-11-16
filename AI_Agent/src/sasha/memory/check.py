"""What the server checks before a note is kept (design §4; the review's M3, M4, M7).

- M4: the owner's message asks to remember (or to forget), with a broad phrase list;
  a question about notes ("what do you remember about my bus?") asks for neither.
- M3, all or nothing: every content word of the note is in the owner's own words of
  this turn (stop words, inflections and the "I" -> "the owner" rewrites aside), and
  every number in it is one the owner said, with the same value. "Remember that" with
  nothing of its own is refused, so a note can't be filled from a reply or a page.
- M7: passwords, PINs, codes, card or account numbers, long digit runs, key-like
  strings, and an email address next to "password" are never stored; `mask` takes the
  secret out of the owner's message before it stays in the history.
The words come from `profile.memory` (English now).
"""

from __future__ import annotations

import re

from sasha.config_memory import MemoryConfig
from sasha.config_timers import TimeCues
from sasha.server.timers.said import tokens

_NUMBERS = TimeCues().number_values  # the timers' number words: "eight" is 8


def _norm(text: str) -> str:
    return " ".join(tokens(text))


def _has(text: str, phrases: list[str]) -> bool:
    padded = f" {_norm(text)} "
    return any(f" {_norm(p)} " in padded for p in phrases)


def is_question(text: str, cfg: MemoryConfig) -> bool:
    words = tokens(text)
    return text.strip().endswith("?") and bool(words) and words[0] in cfg.question_starts


def asks_to_remember(text: str, cfg: MemoryConfig) -> bool:
    return not is_question(text, cfg) and _has(text, cfg.remember_phrases)


def asks_to_forget(text: str, cfg: MemoryConfig) -> bool:
    """ "Forget that note" asks; "don't forget I'm allergic" asks to remember instead."""
    if is_question(text, cfg) or asks_to_remember(text, cfg):
        return False
    return _has(text, cfg.forget_phrases)


def says_everything(text: str, cfg: MemoryConfig) -> bool:
    return bool(set(tokens(text)) & set(cfg.everything_words))


def says_yes(text: str, cfg: MemoryConfig) -> bool:
    norm = _norm(text)
    return any(norm == y or norm.startswith(f"{y} ") for y in map(_norm, cfg.yes_words))


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


def content_words(text: str, cfg: MemoryConfig) -> list[str]:
    """The words that carry meaning: not stop words, not numbers (those are matched by
    value), not the owner rewrites."""
    skip = set(cfg.stop_words) | set(cfg.owner_words) | set(cfg.self_words)
    return [w for w in tokens(text) if w not in skip and _number(w) is None]


def phrase_words(cfg: MemoryConfig) -> set[str]:
    return {w for p in cfg.remember_phrases + cfg.forget_phrases for w in tokens(p)}


def has_content(owner_text: str, cfg: MemoryConfig) -> bool:
    """Anything to remember beyond the request itself? ("Remember that." has none.)"""
    asking = phrase_words(cfg)
    words = [w for w in tokens(owner_text) if w not in asking and w not in cfg.stop_words]
    return any(w not in {"that", "this", "it", "please", "ok", "okay"} for w in words)


def unsaid_words(note: str, owner_text: str, cfg: MemoryConfig) -> list[str]:
    """M3: the note's content words the owner didn't say (empty: the note may be kept)."""
    said = {stem(w) for w in tokens(owner_text)}
    missing = [w for w in content_words(note, cfg) if stem(w) not in said]
    owner, said_words = set(cfg.owner_words), set(tokens(owner_text))
    if owner & set(tokens(note)) and not (set(cfg.self_words) | owner) & said_words:
        missing.append("owner")  # "the owner..." only for the owner's "I", "my", "me"
    return missing


def unsaid_numbers(note: str, owner_text: str) -> list[int]:
    said = {n for n in map(_number, tokens(owner_text)) if n is not None}
    return [n for n in map(_number, tokens(note)) if n is not None and n not in said]


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
    return spans


def has_secret(text: str, cfg: MemoryConfig) -> bool:
    return bool(_secret_spans(text, cfg))


def mask(text: str, cfg: MemoryConfig) -> str:
    """The text with each secret (and what follows a secret word) replaced."""
    spans = sorted(_secret_spans(text, cfg))
    if not spans:
        return text
    out, at = [], 0
    for start, end in spans:
        if start < at:
            at = max(at, end)
            continue
        out.append(text[at:start])
        out.append(cfg.mask)
        at = end
    out.append(text[at:])
    return "".join(out).strip()
