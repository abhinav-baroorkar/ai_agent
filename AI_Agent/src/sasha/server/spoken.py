"""Model text into speakable text, while it streams (sockets opened with `speak=1`).

The model sometimes answers in markdown; the text-to-speech stage then reads the
symbols ("asterisk asterisk") and the Home subtitles show them raw. So before text is
spoken (and on speak=1 sockets, before it is shown), the server:
- drops emphasis marks (`**`, `*`, `__`, `_` at word edges, `~~`), inline code marks,
  code fences, horizontal rules and table separator rows;
- turns headings, list items and table rows into sentences (a full stop where the
  line had none, so the speech pauses);
- speaks a link's text, never its address;
- expands nothing else (numbers, units and abbreviations are left to the TTS stage).
It works on whole lines, and inside a line on whole sentences, so streaming still
starts early; anything that could still be a list marker or an unfinished link is held
back until it is complete, so a marker split across deltas never leaks.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"^(```|~~~)")
_RULE = re.compile(r"^([-*_])(\s*\1){2,}$")  # ---, ***, ___
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_HEADING = re.compile(r"^#{1,6}\s+")
_QUOTE = re.compile(r"^(>\s?)+")
_BULLET = re.compile(r"^([-*+•]|\d{1,3}[.)])\s+")
# Linear (SP2): a link's text can't hold another "[" and its address no bracket, so each
# match stops at the next bracket ("[[[[..." fails at once); an address of any length
# goes whole (SF1: a 300-character one was half spoken).
_ADDRESS = r"[^()\[\]\s]*"
_LINK = re.compile(rf"!?\[([^\[\]\n]{{0,300}})\]\({_ADDRESS}\)")
_LINK_REST = re.compile(rf"\]\({_ADDRESS}\)?|[\[\]]")  # pieces of a link cut apart (SP1)
# SF1: a bare address is said as its host only ("see example.com"), never read out.
_URL = re.compile(r"\b(?:https?://|www\.)([^/\s()\[\]<>]+)[^\s()\[\]<>]*", re.IGNORECASE)
_SKIP_END = re.compile(r"[)\s]")  # where an address that is being dropped ends
# "2*3" and "2 \u00d7 3": a multiplication, not emphasis
_TIMES = re.compile(r"(?<=\d)\s*[*\u00d7]\s*(?=\d)")
_EMPHASIS = re.compile(r"\*+|~~|`+|(?<!\w)_+|_+(?!\w)")
_SPACES = re.compile(r"[ \t]+")
_SENTENCE_END = re.compile(r"[.!?]+\s+")
_STOPS = ".!?:;,"
LINE_START_HOLD = 4  # a partial line is not cut until this many characters (a marker)
MAX_HOLD = 200  # SP1: held text is cut at a soft break past this, so speech can't stall
_SOFT_BREAKS = (", ", "; ", ": ", " ")


def clean_line(line: str, line_start: bool = True, times: str = "times") -> str:
    """One line (or the rest of one) as speakable text. "" if nothing is left to say."""
    text = line.strip()
    if not text:
        return ""
    made_sentence = False
    if line_start:
        if _FENCE.match(text) or _RULE.match(text) or _TABLE_SEP.match(text):
            return ""
        if text.startswith("|") or text.count("|") >= 2:  # a table row: its cells
            text = ", ".join(c.strip() for c in text.strip("|").split("|") if c.strip())
            made_sentence = True
        text = _QUOTE.sub("", text)
        for pattern in (_HEADING, _BULLET):
            text, found = pattern.subn("", text)
            made_sentence = made_sentence or bool(found)
    text = _LINK.sub(r"\1", text)
    text = _LINK_REST.sub("", text)  # a link cut by the hold limit: never its address
    text = _URL.sub(lambda m: m.group(1).removeprefix("www."), text)  # its host only
    text = _TIMES.sub(f" {times} ", text)
    text = _EMPHASIS.sub("", text)
    text = _SPACES.sub(" ", text).strip()
    if made_sentence and text and text[-1] not in _STOPS:
        text += "."  # a list item or heading becomes a sentence: a pause after it
    return text


class SpokenStream:
    """Feed streamed model text; get speakable text out. `text` is all that came out."""

    def __init__(self, times: str = "times") -> None:
        self.buffer = ""
        self.line_open = False  # part of the current line was already released
        self.text = ""
        self.times = times  # how "2*3" is said (profile.prompt_lines, per language)
        self.skipping = False  # dropping an address cut by the hold limit (SF1)

    def _out(self, part: str, line_start: bool) -> str:
        cleaned = clean_line(part, line_start, self.times)
        if not cleaned:
            return ""
        piece = f"{' ' if self.text else ''}{cleaned}"
        self.text += piece
        return piece

    def feed(self, piece: str) -> str:
        self.buffer += piece
        out = ""
        self._skip()
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            out += self._out(line, not self.line_open)
            self.line_open = False
        while True:  # until what is held fits
            cut = self._cut() or self._forced_cut()
            if cut:
                part, self.buffer = self.buffer[:cut], self.buffer[cut:]
                out += self._out(part, not self.line_open)
                self.line_open = True
            if self.skipping:
                self._skip()
                if self.skipping:
                    break  # still inside the address: wait for more text
                continue
            if not cut:
                break
        return out

    def _skip(self) -> None:
        """SF1: drop the rest of an address cut by the hold limit, up to its ")" or the
        next space (kept), so no part of it is ever read out."""
        if not self.skipping:
            return
        end = _SKIP_END.search(self.buffer)
        if end is None:
            self.buffer = ""  # still inside it: drop what came, wait for its end
            return
        stop = end.end() if self.buffer[end.start()] == ")" else end.start()
        self.buffer, self.skipping = self.buffer[stop:], False

    def _forced_cut(self) -> int:
        """SP1: held past MAX_HOLD (a long unclosed link, no sentence end): cut at the
        last soft break before it, or hard at MAX_HOLD. SF1: never inside an address:
        the text before it goes now (with the host, for a bare URL), the rest is
        dropped."""
        if len(self.buffer) <= MAX_HOLD:
            return 0
        window = self.buffer[:MAX_HOLD]
        cut = MAX_HOLD
        for mark in _SOFT_BREAKS:
            at = window.rfind(mark)
            if at > 0:
                cut = at + len(mark)
                break
        start = self._open_address(cut)
        if start is None:
            return cut
        self.skipping = True
        if self.buffer.startswith("](", start):
            return start  # a link: its text is released, the whole address dropped
        url = _URL.match(self.buffer, start)
        return url.end(1) if url is not None else start  # a bare URL: up to its host

    def _open_address(self, cut: int) -> int | None:
        """Where an address still open at `cut` starts: a link's "](" with no ")" or
        space after it, or a bare URL running through the cut."""
        text = self.buffer[:cut]
        link = text.rfind("](")
        if link >= 0 and not _SKIP_END.search(text, link + 2):
            return link
        for m in _URL.finditer(self.buffer):
            if m.start() < cut <= m.end() + 1:
                return m.start()
        return None

    def _cut(self) -> int:
        """Where the open line may be cut now: after its last complete sentence."""
        buf = self.buffer
        start = 0 if self.line_open else len(buf) - len(buf.lstrip())
        if not self.line_open and len(buf) - start < LINE_START_HOLD:
            return 0  # could still be "1. " or "- " at the start of the line
        cut = 0
        for m in _SENTENCE_END.finditer(buf):
            if m.start() >= start + (0 if self.line_open else LINE_START_HOLD - 1):
                cut = m.end()
        if cut and buf.rfind("[", 0, cut) > buf.rfind(")", 0, cut):
            return 0  # inside a link's text or address: wait for it to close
        return cut

    def finish(self) -> str:
        """The rest, at the end of the answer."""
        self._skip()
        if self.skipping:  # the answer ended inside a dropped address
            self.buffer, self.skipping = "", False
        rest, self.buffer = self.buffer, ""
        out = self._out(rest, not self.line_open)
        self.line_open = False
        return out


def to_spoken(text: str, times: str = "times") -> str:
    """A whole answer at once."""
    stream = SpokenStream(times)
    stream.feed(text)
    stream.finish()
    return stream.text
