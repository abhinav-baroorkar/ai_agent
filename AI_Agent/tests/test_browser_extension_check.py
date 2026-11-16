"""C1 means a real installed extension, never Chrome's own state databases (found on
the live machine 2026-11-16: every start after the first was refused). Real headless
Chrome on a temp profile, no network."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sasha.browser.chrome import find_chrome
from sasha.browser.profile import prepare_profile, wait_until_free
from sasha.browser.proxy import FilteringProxy
from sasha.browser.service import BrowserService
from sasha.browser.takeover_files import extensions_present, visible_flags
from sasha.server.launch import browser_env


def test_only_an_installed_extension_counts(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    for rel in ("Extension State", "Extension Rules", "Extension Scripts"):
        db = root / "Default" / rel
        db.mkdir(parents=True)
        for name in ("000003.log", "CURRENT", "LOCK", "LOG", "MANIFEST-000001"):
            (db / name).write_text("")
    (root / "Default" / "Local Extension Settings").mkdir()
    (root / "extensions_crx_cache").mkdir()
    (root / "extensions_crx_cache" / "metadata.json").write_text("{}")
    assert not extensions_present(root)  # exactly what the live profile held
    (root / "Default" / "Extensions" / "Temp").mkdir(parents=True)
    assert not extensions_present(root)  # Chrome's own unpack folder
    (root / "Default" / "Extensions" / "abcdefghijklmnopabcdefghijklmnop").mkdir()
    assert extensions_present(root)


async def _no_dns(host: str, port: int) -> list[Any]:
    raise OSError("no DNS in tests")


@pytest.mark.skipif(find_chrome() is None, reason="no Chrome installed")
def test_the_browser_starts_again_after_a_start_and_after_a_takeover_run(
    tmp_path: Path,
) -> None:
    async def main() -> None:
        service = BrowserService(tmp_path, proxy_factory=lambda: FilteringProxy(_no_dns))
        for _ in range(2):  # a start, a stop, and a start again on the same profile
            async with service.acquire("c"):
                pass
            await service.stop()
        root = prepare_profile(tmp_path)
        assert (root / "Default" / "Extension State").is_dir()  # Chrome made it itself
        assert not extensions_present(root)
        # the take-over window's own flags (headless here: no window on the screen)
        proxy = FilteringProxy(_no_dns)
        port = await proxy.start()
        exe = find_chrome()
        assert exe is not None
        argv = [exe, *visible_flags(root, port, "about:blank"), "--headless=new"]
        proc = subprocess.Popen(argv, env=browser_env(root), cwd=root,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
        await asyncio.sleep(3)
        proc.terminate()
        proc.wait(10)
        await proxy.stop()
        assert await wait_until_free(root)
        assert not extensions_present(root)
        async with service.acquire("c"):  # and the assistant's browser starts again
            pass
        await service.stop()

    asyncio.run(main())
