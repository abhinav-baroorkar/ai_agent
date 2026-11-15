"""Turn a page's accessibility tree into a short text view with numbered elements.

Input: the `nodes` of CDP `Accessibility.getFullAXTree`. Output: plain text (headings,
text, and every interactive element numbered, e.g. `[12] button "Play"`), split into
parts of at most `PART_CHARS` characters (about 2,500 tokens, for the laptop model's
8k context), plus a map from number to the element's DOM node.

Page text is untrusted (review B5): control and invisible formatting characters are
stripped, every name is capped, and nothing here is ever logged. The caller wraps the
result as untrusted data before the model sees it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sasha.browser.redirects import short

PART_CHARS = 10_000
NAME_CHARS = 160
LINE_CHARS = 600  # page text is emitted in lines of about this length
MAX_ELEMENTS = 400
INTERACTIVE = frozenset(
    {
        "link",
        "button",
        "textbox",
        "searchbox",
        "combobox",
        "checkbox",
        "radio",
        "switch",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "tab",
        "slider",
        "spinbutton",
        "option",
        "listbox",
        "treeitem",
    }
)
HEADINGS = frozenset({"heading"})
TEXT = frozenset({"StaticText", "text"})
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Element:
    number: int
    role: str
    name: str
    backend_node_id: int | None


@dataclass
class PageView:
    parts: list[str]
    elements: dict[int, Element] = field(default_factory=dict)
    truncated: bool = False

    def part(self, n: int) -> str:
        """Part `n` (1-based) with a header saying how many there are."""
        if not 1 <= n <= len(self.parts):
            return f"(no part {n}; this page has {len(self.parts)} part(s))"
        return f"(part {n} of {len(self.parts)})\n{self.parts[n - 1]}"


def clean(text: str, limit: int = NAME_CHARS) -> str:
    """Printable text only: no control or invisible format characters (Cc, Cf, Cs,
    Co, Cn), whitespace collapsed, capped."""
    kept = "".join(ch if not unicodedata.category(ch).startswith("C") else " " for ch in text)
    kept = _SPACE.sub(" ", kept).strip()
    return kept if len(kept) <= limit else kept[: limit - 1] + "…"


def _value(node: dict[str, Any], key: str) -> str:
    entry = node.get(key) or {}
    value = entry.get("value") if isinstance(entry, dict) else None
    return str(value) if value is not None else ""


def read_tree(
    nodes: Iterable[dict[str, Any]],
    title: str = "",
    destinations: Mapping[int, str] | None = None,
) -> PageView:
    """`destinations` maps a link's DOM node id to where it really leads (already
    unwrapped from search redirects), shown after the link as `→ host/path`."""
    by_id = {str(n.get("nodeId")): n for n in nodes}
    children = {nid: [str(c) for c in n.get("childIds", [])] for nid, n in by_id.items()}
    child_ids = {c for cs in children.values() for c in cs}
    roots = [nid for nid in by_id if nid not in child_ids]
    lines: list[str] = []
    elements: dict[int, Element] = {}
    truncated = False
    pending_text: list[str] = []

    def flush() -> None:
        if pending_text:
            lines.append(clean(" ".join(pending_text), PART_CHARS))
            pending_text.clear()

    def visit(nid: str, depth: int) -> None:
        nonlocal truncated
        node = by_id.get(nid)
        if node is None or depth > 200:
            return
        role = _value(node, "role")
        raw = _value(node, "name")
        name = clean(raw, PART_CHARS if role in TEXT else NAME_CHARS)  # text isn't cut short
        if not node.get("ignored"):
            if role in INTERACTIVE:
                flush()
                if len(elements) >= MAX_ELEMENTS:
                    truncated = True
                else:
                    number = len(elements) + 1
                    backend = node.get("backendDOMNodeId")
                    elements[number] = Element(
                        number, role, name, int(backend) if backend is not None else None
                    )
                    dest = (destinations or {}).get(int(backend)) if backend is not None else None
                    arrow = f" → {short(dest)}" if dest else ""
                    lines.append(f'[{number}] {role} "{name}"{arrow}')
                return  # an element's own children are its label, already in `name`
            if role in HEADINGS and name:
                flush()
                lines.append(f"## {name}")
                return
            if role in TEXT and name:
                pending_text.append(name)
                if sum(len(t) + 1 for t in pending_text) >= LINE_CHARS:
                    flush()  # long runs of text become several lines, never cut
        for child in children.get(nid, []):
            visit(child, depth + 1)

    for root in roots:
        visit(root, 0)
    flush()
    header = f"Page: {clean(title)}" if title else "Page"
    return PageView(parts=_split([header, *lines]), elements=elements, truncated=truncated)


def _split(lines: list[str]) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > PART_CHARS:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts or [""]
