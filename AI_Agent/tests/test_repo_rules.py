"""Guards for the repo rules in CLAUDE.md."""

from __future__ import annotations

import re
from pathlib import Path

from sasha.config import load_settings
from tests.conftest import REPO_ROOT

MAX_LINES = 400
CODE_DIRS = ["AI_Agent/src", "AI_Agent/tests", "AI_Agent/scripts", "AI_Agent/bench"]


def _code_files() -> list[Path]:
    files: list[Path] = []
    for d in CODE_DIRS:
        files += [p for p in (REPO_ROOT / d).rglob("*") if p.suffix in {".py", ".yaml", ".sh"}]
    return files


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
        if p.suffix in {".py", ".yaml", ".html", ".js"} and pattern.search(p.read_text("utf-8"))
    ]
    assert not offenders, f"Persona name hard-coded in: {offenders}"


SEND_PATTERNS = [
    re.compile(r"def\s+\w*send\w*\s*\("),  # def send_message(...
    re.compile(r"\.send\s*\("),  # messages().send(...) / drafts().send(...)
    re.compile(r"""["']send["']"""),  # getattr(x, "send"), tool name "send"
]


def test_no_send_function_anywhere() -> None:
    """Drafts only: no code path may send email (or anything else) on the owner's behalf."""
    offenders = [
        f"{p.relative_to(REPO_ROOT)}: {pat.pattern}"
        for p in (REPO_ROOT / "AI_Agent/src").rglob("*.py")
        for pat in SEND_PATTERNS
        if pat.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Send-like code found: {offenders}"


def test_tmp_path_stays_inside_the_checkout(tmp_path: Path) -> None:
    """Owner's rule: nothing written outside the project folder, test temp dirs included."""
    assert tmp_path.resolve().is_relative_to(REPO_ROOT.resolve())
