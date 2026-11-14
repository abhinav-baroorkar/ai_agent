"""`sasha serve` (run the brain server) and `sasha window` (open the UI in an app window).

The window uses its own browser profile under `Data/chrome-profile/`, so it never
touches the user's real browser profile. A browser that can't be isolated that way
is not launched silently: we print the URL and say why.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from sasha.config import Settings
from sasha.server.security import is_loopback

CHROMIUM_BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
# A fresh profile must not reach Google or the desktop keyring: no background
# networking, updates, sync or pings; passwords in the profile, not the keyring.
ISOLATION_FLAGS = (
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--no-pings",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--metrics-recording-only",
    "--disable-features=MediaRouter,OptimizationHints",
    # The flags above still left a fresh profile calling Google (network time, account
    # list, Safe Browsing keys, www.google.com), measured with --log-net-log. So the
    # window gets no route out at all: every non-loopback request goes to a dead proxy
    # (Chromium always bypasses the proxy for localhost/127.0.0.1/[::1]) and every DNS
    # lookup fails. The UI must therefore be self-contained (no CDN fonts or scripts).
    "--proxy-server=http://127.0.0.1:9",
    # `MAP *` also catches the 127.0.0.1 literal, so it must be excluded explicitly.
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1",
)
START_TIMEOUT_S = 60.0
BROWSER_TMP = "tmp"  # under the profile dir; see browser_env
WS_MAX_BYTES = 1 << 20  # largest WebSocket message accepted (a 20 ms frame is 640 bytes)


def server_url(host: str, port: int) -> str:
    return f"http://{'[::1]' if host == '::1' else host}:{port}"


def run_server(settings: Settings, host: str | None = None, port: int | None = None) -> int:
    import uvicorn

    from sasha.server.app import create_app
    from sasha.server.deps import build_deps

    cfg = settings.profile.server
    host, port = host or cfg.host, port or cfg.port
    if not is_loopback(host):
        print(
            f"[refused] {host} is not a loopback address. v0 has no auth, so the server "
            "only listens on 127.0.0.1 / localhost / ::1.",
            file=sys.stderr,
        )
        return 2
    # The security middleware checks Host/Origin against the real port.
    settings = settings.model_copy(
        update={
            "profile": settings.profile.model_copy(
                update={"server": cfg.model_copy(update={"host": host, "port": port})}
            )
        }
    )
    deps = build_deps(settings)
    for warning in deps.warnings:
        print(f"[warn] {warning}")
    print(f"Brain server on {server_url(host, port)} (profile {settings.profile.name})")
    uvicorn.run(
        create_app(deps),
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        ws_max_size=WS_MAX_BYTES,
    )
    return 0


def is_running(url: str, timeout_s: float = 1.5) -> bool:
    try:
        resp = httpx.get(f"{url}/api/health", timeout=timeout_s)
        return resp.status_code == 200 and resp.json().get("ok") is True
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def wait_until_up(url: str, proc: subprocess.Popen[bytes], timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if is_running(url):
            return True
        time.sleep(0.3)
    return False


def browser_command(url: str, profile_dir: Path) -> list[str] | None:
    """An isolated app-window command, or None if no Chromium-family browser exists."""
    for name in CHROMIUM_BROWSERS:
        exe = shutil.which(name)
        if exe:
            return [exe, f"--app={url}", f"--user-data-dir={profile_dir}", *ISOLATION_FLAGS]
    return None


def browser_env(profile_dir: Path) -> dict[str, str]:
    """Environment for the window's browser: home and XDG dirs inside the project.

    `--user-data-dir` alone still lets Chromium on Linux write `~/.pki` (NSS cert db),
    `~/.cache` and `~/.config` bits, and its singleton socket and scoped temp files go
    to `/tmp`, so HOME, TMPDIR and the XDG dirs point under the profile. The display
    variables (DISPLAY, WAYLAND_DISPLAY, XAUTHORITY, DBus) are kept; the keyring is
    kept out by `--password-store=basic`.
    """
    env = dict(os.environ)
    if "XAUTHORITY" not in env:  # X11 falls back to $HOME/.Xauthority; keep the real one
        real = Path.home() / ".Xauthority"
        if real.is_file():
            env["XAUTHORITY"] = str(real)
    home = profile_dir / "home"
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CACHE_HOME=str(home / ".cache"),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        # Relative on purpose, and Chrome is started with cwd=profile_dir: its singleton
        # socket lives in TMPDIR, and an absolute path this deep in the project can
        # exceed the 108-byte Unix socket limit (Chrome then exits at once). The
        # SingletonSocket symlink it leaves in the profile resolves the same way.
        TMPDIR=BROWSER_TMP,
    )
    return env


def open_window(
    settings: Settings,
    host: str | None = None,
    port: int | None = None,
    spawn: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    cfg = settings.profile.server
    host, port = host or cfg.host, port or cfg.port
    if not is_loopback(host):  # never probe or open a page on another machine
        print(f"[refused] {host} is not a loopback address.", file=sys.stderr)
        return 2
    url = server_url(host, port)
    server: subprocess.Popen[bytes] | None = None
    if not is_running(url):
        print(f"Starting the brain server on {url} ...")
        cmd = [
            sys.executable,
            "-m",
            "sasha",
            "--profile",
            settings.profile.name,
            "serve",
            "--host",
            host,
            "--port",
            str(port),
        ]
        server = spawn(cmd, env={**os.environ, "SASHA_ROOT": str(settings.paths.root)})
        if not wait_until_up(url, server, START_TIMEOUT_S):
            print("[fail] The server did not start; run `sasha serve` to see why.", file=sys.stderr)
            server.terminate()
            return 1

    profile_dir = settings.paths.data_dir / "chrome-profile"
    window = browser_command(url, profile_dir)
    if window is None:
        print(
            "No Chrome/Chromium found. Not opening your default browser, because it can't "
            f"use a separate profile inside the project. Open {url} yourself."
        )
    else:
        for sub in ("home", "tmp"):
            (profile_dir / sub).mkdir(parents=True, exist_ok=True)
        env = browser_env(profile_dir)
        spawn(
            window,
            env=env,
            cwd=profile_dir,  # TMPDIR is relative to this
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"Opened {url} in an app window.")

    if server is None:
        return 0
    print("Server is running here; press Ctrl-C to stop it.")
    try:
        return server.wait()
    except KeyboardInterrupt:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        return 0
