"""The window at login: one XDG autostart entry, `~/.config/autostart/<DESKTOP_ID>.desktop`.

Why an autostart entry and not a third user service: the desktop session starts it
itself, once per login, with the session's own display and bus variables (a user
service gets them only if the session imported them into the user manager). Closing
the window then stays closed until the next login (a service would need a restart
policy that fights that), and GNOME's "Startup Applications" shows it. The race with
the brain service is handled by `sasha window --attach`, which never starts a brain.
A later screen lock changes nothing: the entry runs only at login.

Written by `sasha autostart on --yes`, next to the services; `off` removes it only if
it carries `MARKER`, so a file of the owner's own is never touched.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sasha.app.launcher import ICON
from sasha.app.menu import desktop_escape, exec_arg
from sasha.app.single import DESKTOP_ID
from sasha.config import Settings

Out = Callable[[str], None]
MARKER = "X-Assistant-Generated-By=sasha autostart"


def entry_path(home: Path) -> Path:
    return home / ".config" / "autostart" / f"{DESKTOP_ID}.desktop"


def entry_text(settings: Settings, python: Path, root: Path) -> str:
    name = settings.assistant.name
    comment = settings.assistant.app.menu_comment.replace("{name}", name)
    exec_line = " ".join([exec_arg(str(python)), "-m", "sasha", "window", "--attach"])
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.5",
            f"Name={desktop_escape(name)}",
            f"Comment={desktop_escape(comment)}",
            f"Exec={desktop_escape(f'env SASHA_ROOT={exec_arg(str(root))} {exec_line}')}",
            f"Path={desktop_escape(str(root))}",
            f"Icon={desktop_escape(str(ICON))}",
            "Terminal=false",
            "NoDisplay=true",  # an autostart entry, not a second menu entry
            "X-GNOME-Autostart-enabled=true",
            f"StartupWMClass={DESKTOP_ID}",
            MARKER,  # `off` removes only a file it made
            "",
        ]
    )


def _ours(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    return MARKER in text.splitlines()


def write_entry(path: Path, text: str, out: Out) -> bool:
    """Write it, unless something of the owner's own is in the way."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        out(f"[not written] {path} is not a plain file; leaving it alone.")
        return False
    if path.is_file() and not _ours(path):
        out(f"[not written] {path} wasn't written by `sasha autostart` (no marker).")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    out(f"Written {path}")
    return True


def remove_entry(path: Path, out: Out) -> bool:
    """Remove it only if this command made it. True when nothing of ours is left."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        out(f"[not removed] {path} is not the file this command writes.")
        return False
    if not path.is_file():
        out(f"No window entry at {path}")
        return True
    if not _ours(path):
        out(f"[not removed] {path} wasn't written by `sasha autostart` (no marker).")
        return False
    path.unlink()
    out(f"Removed {path}")
    return True
