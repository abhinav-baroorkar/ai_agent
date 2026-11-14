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
START_TIMEOUT_S = 60.0


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
    uvicorn.run(create_app(deps), host=host, port=port, log_level="info", access_log=False)
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
            return [
                exe,
                f"--app={url}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
            ]
    return None


def open_window(
    settings: Settings,
    host: str | None = None,
    port: int | None = None,
    spawn: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    cfg = settings.profile.server
    host, port = host or cfg.host, port or cfg.port
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
        profile_dir.mkdir(parents=True, exist_ok=True)
        spawn(window, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
