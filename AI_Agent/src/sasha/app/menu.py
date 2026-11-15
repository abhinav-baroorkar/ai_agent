"""`sasha app-menu on|off|show`: the assistant in the applications menu (app-shell design 7).

One file outside the project, allowed by the owner like the start-at-login links:
`~/.local/share/applications/<DESKTOP_ID>.desktop`. `show` and `on` print it in full
with its path; `on` writes it only with `--yes`; `off` removes it. The name and the
tooltip come from assistant.yaml; the icon is the project's own file (no copy outside).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from sasha.app.launcher import ICON
from sasha.app.single import DESKTOP_ID
from sasha.config import Settings

Out = Callable[[str], None]


def entry_path(home: Path) -> Path:
    return home / ".local" / "share" / "applications" / f"{DESKTOP_ID}.desktop"


def _escape(value: str) -> str:
    """Desktop-entry string values: no line breaks; backslash escaped."""
    return value.replace("\\", "\\\\").replace("\n", " ").replace("\r", " ")


def _exec_arg(value: str) -> str:
    """Desktop-entry Exec quoting: double quotes, with \\ " ` $ escaped, % doubled."""
    for ch in ("\\", '"', "`", "$"):
        value = value.replace(ch, "\\" + ch)
    return '"' + value.replace("%", "%%") + '"'


def entry_text(settings: Settings, python: Path, root: Path) -> str:
    name = settings.assistant.name
    comment = settings.assistant.app.menu_comment.replace("{name}", name)
    exec_line = " ".join(
        [_exec_arg(str(python)), "-m", "sasha", "window"]
    )  # `sasha window` starts the brain if needed, then the shell (or raises it)
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.5",
            f"Name={_escape(name)}",
            f"Comment={_escape(comment)}",
            f"Exec=env SASHA_ROOT={_exec_arg(str(root))} {exec_line}",
            f"Path={_escape(str(root))}",
            f"Icon={_escape(str(ICON))}",
            "Terminal=false",
            "Categories=Utility;",
            f"StartupWMClass={DESKTOP_ID}",
            "StartupNotify=true",
            "",
        ]
    )


def app_menu(
    action: str,
    settings: Settings,
    root: Path,
    yes: bool = False,
    home: Path | None = None,
    python: Path | None = None,
    out: Out = print,
) -> int:
    home = home or Path.home()
    path = entry_path(home)
    if action == "off":
        if path.is_symlink() or path.is_file():
            path.unlink()
            out(f"Removed {path}")
        else:
            out(f"Nothing to remove at {path}")
        return 0
    text = entry_text(settings, python or Path(sys.executable), root)
    out(f"The applications menu entry ({path}):")
    out(text)
    if action == "show":
        return 0
    if not yes:
        out("[not written] Re-run with --yes to add it. Undo: sasha app-menu off")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()  # never write through a link
    path.write_text(text, encoding="utf-8")
    out(f"Written {path}. Undo: sasha app-menu off")
    return 0
