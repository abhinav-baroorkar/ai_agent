"""Guards for the repo rules in CLAUDE.md."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from sasha.config import load_settings
from tests.conftest import REPO_ROOT

MAX_LINES = 400
CODE_DIRS = ["AI_Agent/src", "AI_Agent/tests", "AI_Agent/scripts", "AI_Agent/bench"]
WEB_DIR = "AI_Agent/src/sasha/avatar/web"
WEB_SUFFIXES = {".css", ".js", ".html"}


def _code_files() -> list[Path]:
    files: list[Path] = []
    for d in CODE_DIRS:
        files += [p for p in (REPO_ROOT / d).rglob("*") if p.suffix in {".py", ".yaml", ".sh"}]
    files += [p for p in (REPO_ROOT / WEB_DIR).rglob("*") if p.suffix in WEB_SUFFIXES]
    return files


def test_web_files_are_covered_by_the_line_limit() -> None:
    assert (REPO_ROOT / WEB_DIR / "index.html") in _code_files()


def test_max_400_lines_per_file() -> None:
    too_long = [
        f"{p.relative_to(REPO_ROOT)}: {n}"
        for p in _code_files()
        if (n := len(p.read_text(encoding="utf-8").splitlines())) > MAX_LINES
    ]
    assert not too_long, f"Files over {MAX_LINES} lines: {too_long}"


def test_persona_name_not_hardcoded_in_source() -> None:
    name = load_settings(root=REPO_ROOT, load_env_file=False).assistant.name
    pattern = re.compile(rf"\b{re.escape(name)}\b")  # case-sensitive: package `sasha` is fine
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "AI_Agent/src").rglob("*")
        if p.suffix in {".py", ".yaml", ".html", ".js", ".css"}
        and pattern.search(p.read_text("utf-8"))
    ]
    assert not offenders, f"Persona name hard-coded in: {offenders}"


METHOD_SEND = re.compile(r"\.send\s*\(")  # messages().send(...) / drafts().send(...)
SEND_PATTERNS = [
    re.compile(r"def\s+\w*send\w*\s*\("),  # def send_message(...
    METHOD_SEND,
    re.compile(r"""["']send["']"""),  # getattr(x, "send"), tool name "send"
]
# Forbidden in every file, the exempted ones included: these are how mail leaves a machine.
MAIL_PATTERNS = [
    re.compile(r"\.sendmail\s*\("),
    re.compile(r"\.send_message\s*\("),
    re.compile(r"^\s*(?:import|from)\s+smtplib\b", re.MULTILINE),
    re.compile(r"gmail\.send"),
]
MAIL_IMPORTS = ("smtplib", "email", "googleapiclient", "google")
NETWORK_IMPORTS = ("httpx", "requests", "urllib", "websockets")


class SendExemption(NamedTuple):
    """One file that may write frames to a local transport with a connection's `.send(`."""

    reason: str
    lines: tuple[str, ...]  # the exact source lines (stripped) that are allowed; nothing else
    never_imports: tuple[str, ...]  # modules this file may never import


# Only the `.send(` pattern is relaxed, only for the exact lines listed, and only in these
# files. Everything else in this section still applies to them. A new entry needs the
# reviewer's OK. Nothing under tools/ or connectors/ may ever be listed.
TRANSPORT_SEND_ALLOWED = {
    "src/sasha/listener/indicator/bus.py": SendExemption(
        reason="writes D-Bus messages to the local session bus (tray icon)",
        lines=("self._conn.send(message, serial=serial)",),
        never_imports=MAIL_IMPORTS + NETWORK_IMPORTS,
    ),
    "src/sasha/listener/brain.py": SendExemption(
        reason="WebSocket frames to the local brain (listener transport)",
        lines=("await ws.send(await queue.get())",),
        # httpx stays allowed: it does the loopback-only health check (review L4).
        never_imports=(*MAIL_IMPORTS, "requests", "urllib"),
    ),
}
NEVER_ALLOWED_DIRS = ("src/sasha/tools/", "src/sasha/connectors/")


def _send_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if METHOD_SEND.search(line)]


def _extra_sends(text: str, allowed: tuple[str, ...]) -> list[str]:
    """`.send(` lines beyond the allowed ones, counted: a repeat of an allowed line is extra."""
    return sorted((Counter(_send_lines(text)) - Counter(allowed)).elements())


def _imports(text: str, module: str) -> bool:
    return bool(re.search(rf"^\s*(?:import|from)\s+{re.escape(module)}(?:[.\s,]|$)", text, re.M))


def test_no_send_function_anywhere() -> None:
    """Drafts only: no code path may send email (or anything else) on the owner's behalf."""
    agent = REPO_ROOT / "AI_Agent"
    offenders = []
    for p in (agent / "src").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        exempt = TRANSPORT_SEND_ALLOWED.get(p.relative_to(agent).as_posix())
        for pat in SEND_PATTERNS + MAIL_PATTERNS:
            if not pat.search(text):
                continue
            if pat is METHOD_SEND and exempt and not _extra_sends(text, exempt.lines):
                continue
            offenders.append(f"{p.relative_to(REPO_ROOT)}: {pat.pattern}")
    assert not offenders, f"Send-like code found: {offenders}"


def test_transport_send_allowlist_is_narrow_and_current() -> None:
    """Every exception names a real file, a reason and exact lines; never a tool or connector."""
    for path, exempt in TRANSPORT_SEND_ALLOWED.items():
        assert not path.startswith(NEVER_ALLOWED_DIRS), f"{path} may never be allowlisted"
        assert exempt.reason.strip(), f"{path} needs a reason"
        file = REPO_ROOT / "AI_Agent" / path
        assert file.is_file(), f"Stale allowlist entry: {path}"
        text = file.read_text(encoding="utf-8")
        assert exempt.lines, f"{path} must list its exact allowed lines"
        for line in exempt.lines:
            assert line in _send_lines(text), f"{path} no longer contains: {line}"
        assert set(MAIL_IMPORTS) <= set(exempt.never_imports), f"{path} must ban mail imports"
        for module in exempt.never_imports:
            assert not _imports(text, module), f"{path} must never import {module}"


def test_send_guard_catches_what_it_should() -> None:
    """The exemption is per line, and the mail patterns have no exemption at all."""
    allowed = "self._conn.send(message, serial=serial)"
    assert _send_lines(f"    {allowed}\n") == [allowed]
    assert _extra_sends(f"{allowed}\nother.send(x)\n", (allowed,)) == ["other.send(x)"]
    assert _extra_sends(f"{allowed}\n  {allowed}\n", (allowed,)) == [allowed]  # a second copy
    assert _extra_sends(f"{allowed}\n", (allowed,)) == []
    mail = [
        "server.sendmail(a, b, c)",
        "smtp.send_message(m)",
        "import smtplib",
        "s = 'gmail.send'",
    ]
    for bad in mail:
        assert any(p.search(bad) for p in MAIL_PATTERNS), bad
    assert not any(p.search(allowed) for p in MAIL_PATTERNS)
    assert _imports("import smtplib\n", "smtplib") and _imports("from google.x import y", "google")
    assert not _imports("import googlesomething\n", "google")


def test_tmp_path_stays_inside_the_checkout(tmp_path: Path) -> None:
    """Owner's rule: nothing written outside the project folder, test temp dirs included."""
    assert tmp_path.resolve().is_relative_to(REPO_ROOT.resolve())
