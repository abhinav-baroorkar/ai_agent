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
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)\s]*\)")
_EMPHASIS = re.compile(r"\*+|~~|`+|(?<!\w)_+|_+(?!\w)")
_SPACES = re.compile(r"[ \t]+")
_SENTENCE_END = re.compile(r"[.!?]+\s+")
_STOPS = ".!?:;,"
LINE_START_HOLD = 4  # a partial line is not cut until this many characters (a marker)


def clean_line(line: str, line_start: bool = True) -> str:
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
    text = _EMPHASIS.sub("", text)
    text = _SPACES.sub(" ", text).strip()
    if made_sentence and text and text[-1] not in _STOPS:
        text += "."  # a list item or heading becomes a sentence: a pause after it
    return text


class SpokenStream:
    """Feed streamed model text; get speakable text out. `text` is all that came out."""

    def __init__(self) -> None:
        self.buffer = ""
        self.line_open = False  # part of the current line was already released
        self.text = ""

    def _out(self, part: str, line_start: bool) -> str:
        cleaned = clean_line(part, line_start)
        if not cleaned:
            return ""
        piece = f"{' ' if self.text else ''}{cleaned}"
        self.text += piece
        return piece

    def feed(self, piece: str) -> str:
        self.buffer += piece
        out = ""
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            out += self._out(line, not self.line_open)
            self.line_open = False
        cut = self._cut()
        if cut:
            part, self.buffer = self.buffer[:cut], self.buffer[cut:]
            out += self._out(part, not self.line_open)
            self.line_open = True
        return out

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
        rest, self.buffer = self.buffer, ""
        out = self._out(rest, not self.line_open)
        self.line_open = False
        return out


def to_spoken(text: str) -> str:
    """A whole answer at once."""
    stream = SpokenStream()
    stream.feed(text)
    stream.finish()
    return stream.text
