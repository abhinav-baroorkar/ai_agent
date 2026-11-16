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

from sasha.app.single import DESKTOP_ID, NoRuntimeDir, ask_window, socket_path
from sasha.app.waiting import ATTACH_WAIT_S
from sasha.config import Settings

Spawn = Callable[..., Any]
SESSION_VARIABLES = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")
ICON = Path(__file__).resolve().parents[1] / "avatar" / "web" / "assets" / "icon.svg"


def available() -> bool:
    try:
        return importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None
    except (ImportError, ValueError):
        return False


def has_display(env: dict[str, str] | None = None) -> bool:
    env = dict(os.environ) if env is None else env
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def shell_command(
    settings: Settings, port: int, dead_proxy: int, wake: bool = False, attach: bool = False
) -> list[str]:
    name = settings.assistant.name
    text = settings.assistant.app
    notice = text.listening_notice.replace("{name}", name)
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
    if attach:  # never starts a brain: shows "starting…" until the service is up
        cmd += [
            "--wait-for-brain",
            str(ATTACH_WAIT_S),
            "--starting-notice",
            text.starting_notice.replace("{name}", name),
            "--late-notice",
            text.not_started_notice.replace("{name}", name),
        ]
    return [*cmd, "--wake"] if wake else cmd


def open_shell(
    settings: Settings,
    port: int,
    dead_proxy: int,
    *,
    wake: bool = False,
    as_unit: bool = False,
    attach: bool = False,
    spawn: Spawn = subprocess.Popen,
    ask: Callable[[Path, str], bool] = ask_window,
) -> str:
    """'raised' (one was open), 'started', 'no_display', or 'no_session' (no
    XDG_RUNTIME_DIR: no private place for the window's socket, so no window)."""
    try:
        path = socket_path()
    except NoRuntimeDir:
        return "no_session"
    if ask(path, "wake" if wake else "raise"):
        return "raised"
    if not has_display():
        return "no_display"
    cmd = shell_command(settings, port, dead_proxy, wake=wake, attach=attach)
    env = {**os.environ, "SASHA_ROOT": str(settings.paths.root)}
    if as_unit and shutil.which("systemd-run"):
        unit = ["systemd-run", "--user", f"--unit={DESKTOP_ID}", "--collect", "--quiet"]
        # The unit gets the user manager's environment, which may lack the session's
        # display or bus (e.g. an older login import): pass what this session has.
        props = [f"--setenv=SASHA_ROOT={settings.paths.root}"] + [
            f"--setenv={name}={os.environ[name]}"
            for name in SESSION_VARIABLES
            if os.environ.get(name)
        ]
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
