"""Start the assistant's headless Chrome: isolated, filtered, pipe-only (review B6).

- Its own profile under `Data/browser-profile/` (the owner's real profile is refused),
  and the window launcher's isolation: own HOME/XDG dirs, relative TMPDIR, basic
  password store, no first-run, sync, pings, updates or background networking.
- All traffic through our filtering proxy, loopback included (`<-loopback>`); Chrome
  resolves no names itself; no QUIC and no non-proxied UDP.
- DevTools over `--remote-debugging-pipe` only. If the pipe doesn't answer, Chrome is
  killed and start-up fails: there is never a fallback to a debugging port.
- `--disable-gpu` (the GPU is the LLM's), `--mute-audio` (phase 1, B3), downloads
  denied, every permission denied, and the death-signal wrapper so a crashed brain
  leaves no browser behind.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from sasha.browser.cdp import CDPError, CDPPipe
from sasha.browser.history import forget_history
from sasha.listener.capture import with_death_signal
from sasha.server.launch import CHROMIUM_BROWSERS, ISOLATION_FLAGS, browser_env

log = logging.getLogger(__name__)

PIPE_TIMEOUT_S = 10.0
DENIED_PERMISSIONS = (
    "microphone",
    "camera",
    "geolocation",
    "notifications",
    "midi",
    "display-capture",
    "clipboard-read",
    "clipboard-write",
    "local-fonts",
    "window-management",
    "idle-detection",
    "storage-access",
    "background-sync",
    "sensors",
)
REQUIRED_DENIALS = ("microphone", "camera")  # start fails if these can't be denied


class BrowserUnavailableError(RuntimeError):
    pass


def profile_dir(data_dir: Path) -> Path:
    return data_dir / "browser-profile"


def check_profile(path: Path, data_dir: Path) -> Path:
    """Only our own profile folder, never the owner's Chrome profile."""
    resolved, expected = path.resolve(), profile_dir(data_dir).resolve()
    if resolved != expected:
        raise BrowserUnavailableError(f"Refusing a browser profile outside {expected}")
    return resolved


# Chrome's own background traffic, switched off at the source where a switch exists
# (review question 1); what remains is refused by hostname in the proxy.
BACKGROUND_FLAGS = (
    "--disable-client-side-phishing-detection",
    "--safebrowsing-disable-auto-update",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--no-service-autorun",
    "--disable-search-engine-choice-screen",
)
BACKGROUND_FEATURES_OFF = (
    "BackForwardCache",  # so "back" is a normal load whose end we can see
    "Translate",
    "AutofillServerCommunication",
    "NetworkTimeServiceQuerying",
    "CertificateTransparencyComponentUpdater",
    "OptimizationGuideModelDownloading",
    "MediaRouter",
    "OptimizationHints",
    "PreconnectToSearch",
    "SafeBrowsingHashPrefixRealTimeLookups",  # the www.gstatic.com ohttp key fetch
    "HashPrefixRealTimeLookups",
)


def chrome_flags(profile: Path, proxy_port: int) -> list[str]:
    replaced = ("--host-resolver-rules=", "--disable-features=")
    isolation = [f for f in ISOLATION_FLAGS if not f.startswith(replaced)]
    window_off = next(
        (f.split("=", 1)[1] for f in ISOLATION_FLAGS if f.startswith("--disable-features=")), ""
    )
    # One --disable-features flag (a second would replace the first).
    features = [*window_off.split(","), *BACKGROUND_FEATURES_OFF]
    disabled = ",".join(dict.fromkeys(x for x in features if x))
    return [
        "--headless=new",
        "--remote-debugging-pipe",
        f"--user-data-dir={profile}",
        *isolation,
        f"--disable-features={disabled}",
        "--disable-gpu",
        "--mute-audio",
        "--disable-quic",
        "--deny-permission-prompts",
        *BACKGROUND_FLAGS,
        f"--proxy-server=http://127.0.0.1:{proxy_port}",
        "--proxy-bypass-list=<-loopback>",
        # Chrome resolves nothing itself; only the proxy's own address is reachable.
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
        "about:blank",
    ]


BASH = shutil.which("bash") or "/bin/bash"


def pipe_fd_script(read_fd: int, write_fd: int) -> str:
    """Shell code that puts the pipe ends on fds 3 (Chrome reads) and 4 (Chrome writes).

    Moves them to free high fds first: os.pipe() may itself have returned 3 or 4, and a
    direct `exec 3<&3 ... 3<&-` would close what it just set. Needs bash: dash accepts
    only single-digit fd numbers."""
    t1 = _free_temp(read_fd, write_fd, 100)  # even numbers
    t2 = _free_temp(read_fd, write_fd, 101)  # odd numbers, so never equal to t1
    return (
        f"exec {t1}<&{read_fd} {t2}>&{write_fd}; exec {read_fd}<&- {write_fd}>&-; "
        f'exec 3<&{t1} 4>&{t2}; exec {t1}<&- {t2}>&-; exec "$@"'
    )


def _free_temp(a: int, b: int, start: int) -> int:
    n = start
    while n in (a, b):
        n += 2
    return n


def find_chrome() -> str | None:
    for name in CHROMIUM_BROWSERS:
        if path := shutil.which(name):
            return path
    return None


@dataclass
class Chrome:
    process: asyncio.subprocess.Process
    pipe: CDPPipe
    profile: Path
    version: str

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self.pipe.call("Browser.close", timeout=3)
        if self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.process.wait(), 5)
        with contextlib.suppress(Exception):
            await self.pipe.close()


async def start_chrome(data_dir: Path, proxy_port: int, exe: str | None = None) -> Chrome:
    exe = exe or find_chrome()
    if exe is None:
        raise BrowserUnavailableError("No Chrome or Chromium found")
    profile = check_profile(profile_dir(data_dir), data_dir)
    (profile / "home").mkdir(parents=True, exist_ok=True, mode=0o700)
    (profile / "tmp").mkdir(exist_ok=True, mode=0o700)
    (profile / "DevToolsActivePort").unlink(missing_ok=True)
    forget_history(profile)  # in case the last run was killed before it could
    to_chrome_r, to_chrome_w = os.pipe()  # Chrome reads commands on its fd 3
    from_chrome_r, from_chrome_w = os.pipe()  # Chrome writes replies on its fd 4
    argv, hook = with_death_signal(
        [
            BASH,
            "-c",
            pipe_fd_script(to_chrome_r, from_chrome_w),
            "bash",
            exe,
            *chrome_flags(profile, proxy_port),
        ]
    )
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=profile,
            env=browser_env(profile),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            pass_fds=(to_chrome_r, from_chrome_w),
            preexec_fn=hook,
        )
    finally:
        os.close(to_chrome_r)
        os.close(from_chrome_w)
    pipe = CDPPipe(from_chrome_r, to_chrome_w)
    await pipe.open()
    try:
        version = await pipe.call("Browser.getVersion", timeout=PIPE_TIMEOUT_S)
    except CDPError as e:
        # The child may have exited and been reaped already (a crash at start, or a
        # program that isn't Chrome): cleaning up must never replace the real error.
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await pipe.close()
        raise BrowserUnavailableError(
            "Chrome did not answer on the DevTools pipe; not falling back to a port"
        ) from e
    chrome = Chrome(process, pipe, profile, str(version.get("product", "?")))
    try:
        await lock_down(chrome)
    except Exception:
        await chrome.stop()
        raise
    return chrome


async def lock_down(chrome: Chrome) -> None:
    """Refuse if a debugging port appeared; deny downloads and permissions."""
    if (chrome.profile / "DevToolsActivePort").exists():
        raise BrowserUnavailableError("Chrome opened a DevTools port; refusing")
    pipe = chrome.pipe
    await pipe.call("Browser.setDownloadBehavior", {"behavior": "deny"})
    for name in DENIED_PERMISSIONS:
        try:
            await pipe.call(
                "Browser.setPermission", {"permission": {"name": name}, "setting": "denied"}
            )
        except CDPError:
            if name in REQUIRED_DENIALS:
                raise BrowserUnavailableError(f"Could not deny the {name} permission") from None
            log.info("Permission %s not known to this Chrome", name)


async def new_page(chrome: Chrome) -> tuple[str, str]:
    """Open a page target; returns (target_id, session_id) for a flattened session."""
    target = await chrome.pipe.call("Target.createTarget", {"url": "about:blank"})
    attached = await chrome.pipe.call(
        "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
    )
    return str(target["targetId"]), str(attached["sessionId"])
