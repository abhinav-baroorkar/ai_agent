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


def test_gmail_has_no_send_function() -> None:
    src = (REPO_ROOT / "AI_Agent/src/sasha/tools/gmail.py").read_text(encoding="utf-8")
    assert not re.search(r"def\s+\w*send\w*\s*\(", src)
    assert ".send(" not in src
