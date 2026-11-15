"""The real headless Chrome: pipe only, isolation, refusals (skipped without Chrome)."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from sasha.browser.chrome import (
    BrowserUnavailableError,
    check_profile,
    chrome_flags,
    find_chrome,
    pipe_fd_script,
    profile_dir,
    start_chrome,
)
from sasha.browser.proxy import FilteringProxy
from tests.conftest import REPO_ROOT

needs_chrome = pytest.mark.skipif(find_chrome() is None, reason="no Chrome installed")


async def _no_dns(host: str, port: int) -> list[Any]:
    raise OSError("no DNS in tests")


def test_flags_are_pipe_only_and_locked_down(tmp_path: Path) -> None:
    flags = chrome_flags(tmp_path, 4242)
    for must in (
        "--headless=new",
        "--remote-debugging-pipe",
        "--disable-gpu",
        "--mute-audio",
        "--disable-quic",
        "--proxy-server=http://127.0.0.1:4242",
        "--proxy-bypass-list=<-loopback>",
        "--password-store=basic",
        "--no-first-run",
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    ):
        assert must in flags, must
    for must in ("--disable-background-networking", "--disable-component-update", "--no-pings"):
        assert must in flags, must
    features = [f for f in flags if f.startswith("--disable-features=")]
    assert len(features) == 1  # a second one would replace the first
    off = features[0].split("=", 1)[1].split(",")
    for name in ("BackForwardCache", "Translate", "AutofillServerCommunication", "MediaRouter"):
        assert name in off, name
    assert len(off) == len(set(off))
    assert not any(f.startswith("--remote-debugging-port") for f in flags)
    assert sum(f.startswith("--host-resolver-rules") for f in flags) == 1


def test_only_our_own_profile(tmp_path: Path) -> None:
    assert check_profile(profile_dir(tmp_path), tmp_path) == profile_dir(tmp_path).resolve()
    for other in (Path.home() / ".config" / "google-chrome", tmp_path / "other"):
        with pytest.raises(BrowserUnavailableError):
            check_profile(other, tmp_path)


def test_pipe_fds_land_on_3_and_4_even_when_the_pipe_is_3_and_4() -> None:
    script = pipe_fd_script(3, 4)
    out = subprocess.run(
        [
            "bash",
            "-c",
            "exec 3</dev/null 4>/dev/null; " + script,
            "bash",
            "ls",
            "-l",
            "/proc/self/fd",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    fds = {line.split()[-3] for line in out.stdout.splitlines() if "->" in line}
    assert {"3", "4"} <= fds, out.stdout


def test_a_program_that_does_not_speak_the_pipe_is_refused(tmp_path: Path) -> None:
    """Never a fallback to a port: no answer on the pipe means no browser."""

    async def main() -> None:
        proxy = FilteringProxy(_no_dns)
        port = await proxy.start()
        try:
            with pytest.raises(BrowserUnavailableError, match="not falling back to a port"):
                await start_chrome(tmp_path, port, exe="/bin/true")
        finally:
            await proxy.stop()

    asyncio.run(main())


@needs_chrome
def test_real_chrome_starts_on_the_pipe_with_no_port(tmp_path: Path) -> None:
    async def main() -> None:
        proxy = FilteringProxy(_no_dns)
        chrome = await start_chrome(tmp_path, await proxy.start())
        try:
            assert chrome.version.startswith(("Chrome/", "HeadlessChrome/", "Chromium/"))
            assert not (chrome.profile / "DevToolsActivePort").exists()
            nav_target = await chrome.pipe.call("Target.getTargets")
            assert nav_target["targetInfos"]
        finally:
            await chrome.stop()
            await proxy.stop()
        assert chrome.process.returncode is not None

    asyncio.run(main())


@needs_chrome
def test_kill_9_of_the_brain_leaves_no_chrome(tmp_path: Path) -> None:
    code = textwrap.dedent(f"""
        import asyncio, sys
        from pathlib import Path
        sys.path.insert(0, {str(REPO_ROOT / "AI_Agent")!r})
        from sasha.browser.chrome import start_chrome
        from sasha.browser.proxy import FilteringProxy
        async def main():
            proxy = FilteringProxy()
            chrome = await start_chrome(Path({str(tmp_path)!r}), await proxy.start())
            print(chrome.process.pid, flush=True)
            await asyncio.sleep(60)
        asyncio.run(main())
    """)
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert parent.stdout is not None
        chrome_pid = int(parent.stdout.readline())
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(chrome_pid, 0)
                with open(f"/proc/{chrome_pid}/stat") as f:
                    if f.read().split()[2] == "Z":
                        break
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Chrome outlived the brain")
    finally:
        if parent.poll() is None:
            parent.kill()


def test_forget_history_removes_url_files_and_keeps_the_rest(tmp_path: Path) -> None:
    from sasha.browser.history import forget_history

    profile = tmp_path / "browser-profile"
    default = profile / "Default"
    (default / "Sessions").mkdir(parents=True)
    (default / "Sessions" / "Session_1").write_text("https://x/?q=secret")
    for name in ("History", "History-journal", "Favicons", "Cookies", "Preferences"):
        (default / name).write_text("data")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (default / "Cache").symlink_to(outside)  # a link out of the profile is only unlinked
    assert forget_history(profile) == 5
    assert sorted(p.name for p in default.iterdir()) == ["Cookies", "Preferences"]
    assert (outside / "keep.txt").exists()
    assert forget_history(tmp_path / "missing") == 0
