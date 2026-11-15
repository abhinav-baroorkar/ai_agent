"""Guards for the repo rules in CLAUDE.md."""

from __future__ import annotations

import re
from pathlib import Path

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
# Files that may call a connection's own `.send(` because they write frames to a local
# transport, not messages on the owner's behalf. Path (relative to AI_Agent/) -> reason.
# Only the `.send(` pattern is relaxed for them; the other two stay forbidden everywhere.
# A new entry needs the reviewer's OK. Nothing under tools/ or connectors/ may ever be here.
TRANSPORT_SEND_ALLOWED = {
    "src/sasha/listener/indicator/bus.py": (
        "writes D-Bus messages to the local session bus (tray icon)"
    ),
}
NEVER_ALLOWED_DIRS = ("src/sasha/tools/", "src/sasha/connectors/")


def test_no_send_function_anywhere() -> None:
    """Drafts only: no code path may send email (or anything else) on the owner's behalf."""
    agent = REPO_ROOT / "AI_Agent"
    offenders = [
        f"{p.relative_to(REPO_ROOT)}: {pat.pattern}"
        for p in (agent / "src").rglob("*.py")
        for pat in SEND_PATTERNS
        if pat.search(p.read_text(encoding="utf-8"))
        and not (pat is METHOD_SEND and p.relative_to(agent).as_posix() in TRANSPORT_SEND_ALLOWED)
    ]
    assert not offenders, f"Send-like code found: {offenders}"


def test_transport_send_allowlist_is_narrow_and_current() -> None:
    """Every exception names a real file with a reason, and never a tool or a connector."""
    for path, reason in TRANSPORT_SEND_ALLOWED.items():
        assert not path.startswith(NEVER_ALLOWED_DIRS), f"{path} may never be allowlisted"
        assert reason.strip(), f"{path} needs a reason"
        file = REPO_ROOT / "AI_Agent" / path
        assert file.is_file(), f"Stale allowlist entry: {path}"
        assert METHOD_SEND.search(file.read_text(encoding="utf-8")), f"{path} no longer needs it"


def test_tmp_path_stays_inside_the_checkout(tmp_path: Path) -> None:
    """Owner's rule: nothing written outside the project folder, test temp dirs included."""
    assert tmp_path.resolve().is_relative_to(REPO_ROOT.resolve())
