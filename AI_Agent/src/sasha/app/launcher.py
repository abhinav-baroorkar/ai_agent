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

from sasha.app import start_check
from sasha.app.single import DESKTOP_ID, NoRuntimeDir, ask_window, socket_path
from sasha.app.waiting import ATTACH_WAIT_S
from sasha.config import Settings

Spawn = Callable[..., Any]
UNIT_START_S = 5.0  # how long systemd-run may take to start the window unit
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
        # the shell reads the brain's per-launch token itself (a path, never the token)
        "--state-dir",
        str(settings.paths.state_dir),
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
    unit_alive: Callable[[], bool] | None = None,
    check_s: float = start_check.START_CHECK_S,
) -> str:
    """'raised' (one was open), 'started', 'failed' (spawned, but it never came up: the
    reason is in `start_check.log_path`), 'no_display', or 'no_session' (no
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
        log = start_check.log_path(settings)
        os.close(start_check.open_log(settings))  # 0600, then the unit appends to it
        props += [
            "--property=StandardOutput=null",
            f"--property=StandardError=append:{log}",
        ]
        # Wait for systemd-run itself: it returns once the unit has started. Checking the
        # unit before that read "inactive", so every wake-open was called a failure and
        # the Chrome window opened next to a shell that was still starting (2026-11-16).
        proc = spawn([*unit, *props, *cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = _wait(proc)
        if rc != 0:
            start_check.note(settings, f"systemd-run could not start the window unit (exit {rc})")
            return "failed"
        alive = unit_alive or _unit_alive
    else:
        fd = start_check.open_log(settings)
        try:
            proc = spawn(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=fd,  # its first lines only: it silences itself once it is up
                start_new_session=True,  # outlives the terminal that ran `sasha window`
            )
        finally:
            os.close(fd)

        def alive() -> bool:
            return proc is None or proc.poll() is None

    up = start_check.confirm(lambda: ask(path, "ping"), alive, timeout=check_s)
    return "started" if up else "failed"


def _wait(proc: Any, timeout: float = UNIT_START_S) -> int:
    """systemd-run's exit code (0 for a stand-in spawn that returns no process)."""
    if proc is None or not hasattr(proc, "wait"):
        return 0
    try:
        return int(proc.wait(timeout=timeout))
    except subprocess.TimeoutExpired:
        return 0  # still waiting on the user manager: the start check decides


def _unit_alive(run: Callable[..., Any] = subprocess.run) -> bool:
    """The transient unit is running (or starting); it is gone once the shell exits."""
    try:
        out = run(
            [
                "systemctl",
                "--user",
                "show",
                "-p",
                "ActiveState",
                "--value",
                f"{DESKTOP_ID}.service",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # can't tell: the ping and the timeout decide
    return out.stdout.strip() not in ("failed", "inactive")
