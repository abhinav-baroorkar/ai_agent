"""Start the app shell, or bring the open one up (used by `sasha window` and the listener).

- If a shell is running, it is asked over its socket (`single.send`): `raise`, or `wake`.
- Otherwise it is started. From `sasha window` (a terminal), as a detached process; from
  the listener (a systemd user service), as its own transient user unit with
  `systemd-run --user --unit=<DESKTOP_ID>`, so it doesn't share (and die with) the
  listener's cgroup, and the unit name is a second single-copy lock.
- `available()` is False without the `app` extra: callers then use the Chrome window.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sasha.app.single import DESKTOP_ID, ask_window, socket_path
from sasha.config import Settings

Spawn = Callable[..., Any]
ICON = Path(__file__).resolve().parents[1] / "avatar" / "web" / "assets" / "icon.svg"


def available() -> bool:
    try:
        return importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None
    except (ImportError, ValueError):
        return False


def has_display(env: dict[str, str] | None = None) -> bool:
    env = dict(os.environ) if env is None else env
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def shell_command(settings: Settings, port: int, dead_proxy: int, wake: bool = False) -> list[str]:
    name = settings.assistant.name
    notice = settings.assistant.app.listening_notice.replace("{name}", name)
    cmd = [
        sys.executable,
        "-m",
        "sasha.app.shell",
        "--port",
        str(port),
        "--dead-proxy",
        str(dead_proxy),
        "--profile",
        str(settings.paths.data_dir / "app-profile"),
        "--title",
        name,
        "--notice",
        notice,
        "--icon",
        str(ICON),
    ]
    return [*cmd, "--wake"] if wake else cmd


def open_shell(
    settings: Settings,
    port: int,
    dead_proxy: int,
    *,
    wake: bool = False,
    as_unit: bool = False,
    spawn: Spawn = subprocess.Popen,
    ask: Callable[[Path, str], bool] = ask_window,
) -> str:
    """'raised' (one was open), 'started', or 'no_display'."""
    if ask(socket_path(), "wake" if wake else "raise"):
        return "raised"
    if not has_display():
        return "no_display"
    cmd = shell_command(settings, port, dead_proxy, wake=wake)
    env = {**os.environ, "SASHA_ROOT": str(settings.paths.root)}
    if as_unit and shutil.which("systemd-run"):
        unit = ["systemd-run", "--user", f"--unit={DESKTOP_ID}", "--collect", "--quiet"]
        props = [f"--setenv=SASHA_ROOT={settings.paths.root}"]
        spawn([*unit, *props, *cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        spawn(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # outlives the terminal that ran `sasha window`
        )
    return "started"
