"""Guards for the web UI (`src/sasha/avatar/web`): no HTML-injection sinks, no outside URLs.

The page shows text that comes from a language model, device names and connector
messages, all untrusted. It may only ever put them on screen as text, and it may not
load or call anything outside this computer.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import REPO_ROOT

WEB = REPO_ROOT / "AI_Agent/src/sasha/avatar/web"
TEXT_SUFFIXES = {".html", ".js", ".css", ".svg"}
FORBIDDEN = [
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
    "srcdoc",
]
URL = re.compile(r"https?://[^\s\"'`)<>]*")
ALLOWED_URLS = {"http://www.w3.org/2000/svg"}  # the SVG namespace, never fetched


def _web_files() -> list[Path]:
    return sorted(p for p in WEB.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES)


def test_web_folder_is_found() -> None:
    assert (WEB / "index.html").is_file()
    assert len(_web_files()) > 5


def test_no_html_injection_sinks() -> None:
    offenders = [
        f"{p.relative_to(WEB)}: {word}"
        for p in _web_files()
        for word in FORBIDDEN
        if word in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"Forbidden in the web UI: {offenders}"


def test_no_outside_urls() -> None:
    offenders = [
        f"{p.relative_to(WEB)}: {url}"
        for p in _web_files()
        for url in URL.findall(p.read_text(encoding="utf-8"))
        if url not in ALLOWED_URLS
    ]
    assert not offenders, f"Outside URLs in the web UI: {offenders}"


def test_every_web_file_is_a_known_type() -> None:
    unknown = [
        str(p.relative_to(WEB))
        for p in WEB.rglob("*")
        if p.is_file() and p.suffix not in TEXT_SUFFIXES
    ]
    assert not unknown, f"Unexpected file types in the web UI: {unknown}"
