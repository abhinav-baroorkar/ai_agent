"""`sasha serve` (run the brain server) and `sasha window` (open the UI in an app window).

The window uses its own browser profile under `Data/chrome-profile/`, so it never
touches the user's real browser profile. A browser that can't be isolated that way
is not launched silently: we print the URL and say why.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from sasha.config import Settings
from sasha.server.security import is_loopback
from sasha.server.warmup import WARMUP_TIMEOUT_S
from sasha.server.window_token import read_token, write_start_page

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
    # WebRTC can open UDP straight to the network, around the proxy below.
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    # The flags above still left a fresh profile calling Google (network time, account
    # list, Safe Browsing keys, www.google.com), measured with --log-net-log. So the
    # window gets no route out at all: every non-loopback request goes to a dead proxy
    # (see `dead_proxy_port`; Chromium always bypasses the proxy for loopback) and every
    # DNS lookup fails. The UI must therefore be self-contained (no CDN fonts or scripts).
    # `MAP *` also catches IP literals, so each loopback address is excluded explicitly.
    # IPv6 is written bare (`::1`): `[::1]` doesn't match, and a window on ::1 then
    # failed to load (checked live with --log-net-log).
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1, EXCLUDE ::1",
)
DEAD_PROXY_PORT = 9  # "discard"; normally nothing listens on it
# Startup = imports + LLM warm-up (capped at WARMUP_TIMEOUT_S) + loading the voice
# stages. The margin covers imports and the voice stages on a cold disk.
START_MARGIN_S = 45.0
START_TIMEOUT_S = WARMUP_TIMEOUT_S + START_MARGIN_S
PROGRESS_EVERY_S = 10.0
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


def wait_until_up(
    url: str,
    proc: subprocess.Popen[bytes],
    timeout_s: float,
    say: Callable[[str], None] = lambda line: print(line, flush=True),
    every_s: float = PROGRESS_EVERY_S,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait for the server's health check, printing a line every `every_s` seconds."""
    start = clock()
    next_note = start + every_s
    while (now := clock()) < start + timeout_s:
        if proc.poll() is not None:
            return False
        if is_running(url):
            return True
        if now >= next_note:
            say(f"  still starting ({now - start:.0f} s): loading the language and voice models")
            next_note += every_s
        time.sleep(0.3)
    return False


def port_is_closed(port: int, timeout_s: float = 0.3) -> bool:
    """True if nothing accepts TCP connections on 127.0.0.1:<port>."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def dead_proxy_candidates() -> tuple[int, ...]:
    """Port 9 first, then the other odd ports below 1024."""
    return (DEAD_PROXY_PORT, *(p for p in range(1, 1024, 2) if p != DEAD_PROXY_PORT))


def dead_proxy_port(is_closed: Callable[[int], bool] = port_is_closed) -> int:
    """A closed privileged local port, for the window's proxy that goes nowhere.

    Only ports below 1024: no non-root process can bind them later, so nothing can
    start listening there while the window is open and receive its proxied traffic
    (CONNECT lines naming hosts, plain-HTTP URLs). An ephemeral port could be.
    """
    for port in dead_proxy_candidates():
        if is_closed(port):
            return port
    raise RuntimeError("found no closed privileged local port for the window's dead proxy")


def browser_command(
    url: str, profile_dir: Path, proxy_port: int = DEAD_PROXY_PORT
) -> list[str] | None:
    """An isolated app-window command, or None if no Chromium-family browser exists."""
    for name in CHROMIUM_BROWSERS:
        exe = shutil.which(name)
        if exe:
            return [
                exe,
                f"--app={url}",
                f"--user-data-dir={profile_dir}",
                *ISOLATION_FLAGS,
                f"--proxy-server=http://127.0.0.1:{proxy_port}",
            ]
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
    try:
        proxy_port = dead_proxy_port()  # checked before anything starts
    except RuntimeError as exc:
        print(f"[fail] Not opening the window: {exc}.", file=sys.stderr)
        return 1
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
            print(
                f"[fail] The server did not start within {START_TIMEOUT_S:.0f} s; "
                "run `sasha serve` to see why.",
                file=sys.stderr,
            )
            server.terminate()
            return 1

    profile_dir = settings.paths.data_dir / "chrome-profile"
    token = read_token(settings.paths.state_dir)
    if token is None:  # an older brain: the window works, its browser panel doesn't
        print("[warn] No window token from the brain; the browser panel will be refused.")
        start = url
    else:  # opened on a private local page, so the token is never on a command line
        start = write_start_page(profile_dir, url, token).as_uri()
    window = browser_command(start, profile_dir, proxy_port)
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
