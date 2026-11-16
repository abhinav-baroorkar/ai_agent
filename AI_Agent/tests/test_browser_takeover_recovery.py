"""Take-over review K1-K4 and S1-S2: site storage, a crash mid take-over, a restore that
fails half-way, and hand-back racing the switch. Fakes only: no window, no Chrome."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import sasha.browser.service as service_module
import sasha.browser.takeover as takeover_module
import sasha.browser.takeover_cleanup as cleanup_module
from sasha.browser.profile import prepare_profile
from sasha.browser.proxy import FilteringProxy
from sasha.browser.service import BrowserBusyError, BrowserService
from sasha.browser.takeover_cleanup import STORAGE_WIPE, marker_path, pending
from sasha.browser.takeover_files import BLOCK_ALL_DOWNLOADS, seed_takeover_preferences
from tests.test_browser_takeover import _rig, _until
from tests.test_browser_view import FakeChrome


def _site_storage(root: Path) -> list[Path]:
    made = [
        root / "Default" / "Local Storage" / "leveldb" / "000003.log",
        root / "Default" / "IndexedDB" / "https_other.example_0.indexeddb.leveldb" / "x",
        root / "Default" / "Service Worker" / "Database" / "x",
    ]
    for path in made:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("signed in")
    return made


def _filtered(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def keep(chrome: Any, site: str) -> int:
        seen.append(site)
        return 0

    monkeypatch.setattr(service_module, "keep_only_site_data", keep)
    return seen


def test_k1_site_storage_never_survives_a_hand_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def main() -> None:
        service, t, spawned = _rig(tmp_path, monkeypatch)
        await t.start()
        await _until(lambda: t.mode == "owner")
        made = _site_storage(prepare_profile(tmp_path))  # his window kept a sign-in
        spawned[0][2].closed.set()
        await _until(lambda: t.mode == "assistant")
        assert not any(p.exists() for p in made)
        assert not any((prepare_profile(tmp_path) / rel).exists() for rel in STORAGE_WIPE)
        assert not pending(tmp_path)
        await service.stop()

    asyncio.run(main())


def test_k2_a_crash_mid_take_over_is_finished_at_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filtered = _filtered(monkeypatch)

    async def main() -> None:
        _, t, _ = _rig(tmp_path, monkeypatch)
        await t.start()
        await _until(lambda: t.mode == "owner")
        root = prepare_profile(tmp_path)
        made = _site_storage(root)
        (root / "Default" / "Web Data").write_text("autofill")
        assert json.loads(marker_path(tmp_path).read_text()) == {"engine_site": "duckduckgo.com"}
        assert marker_path(tmp_path).stat().st_mode & 0o777 == 0o600
        # the brain stops here (his window dies with it); a new brain on the same folder

        async def starter(data_dir: Path, port: int) -> Any:
            return FakeChrome()

        fresh = BrowserService(tmp_path, starter=starter, proxy_factory=FilteringProxy)
        async with fresh.acquire("conversation-x"):
            pass
        assert filtered == ["duckduckgo.com"]  # only the engine's cookies (C3)
        assert not any(p.exists() for p in made) and not (root / "Default" / "Web Data").exists()
        assert not pending(tmp_path)  # marker and snapshot gone
        await fresh.stop()

    asyncio.run(main())


def test_k3_a_half_failed_restore_keeps_bots_out_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(takeover_module, "RETRY_S", 0.05)
    real = cleanup_module.restore_config
    broken = {"on": True}

    def restore(root: Path, keep: Path) -> None:
        if broken["on"]:
            raise PermissionError("can't write")
        real(root, keep)

    monkeypatch.setattr(cleanup_module, "restore_config", restore)

    async def main() -> None:
        service, t, spawned = _rig(tmp_path, monkeypatch)
        await t.start()
        await _until(lambda: t.mode == "owner")
        spawned[0][2].closed.set()
        await _until(lambda: t.mode == "restoring")
        assert service.driving and marker_path(tmp_path).exists()
        with pytest.raises(BrowserBusyError):
            async with service.acquire("conversation-b"):
                pass
        assert service.keep_cookies_of == "duckduckgo.com"  # set first, whatever failed
        broken["on"] = False  # e.g. the permission problem is gone
        await _until(lambda: t.mode == "assistant")
        assert not service.driving and not pending(tmp_path)
        await service.stop()

    asyncio.run(main())


def test_k4_hand_back_during_the_switch_never_launches_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def main() -> None:
        service, t, spawned = _rig(tmp_path, monkeypatch)
        async with service.acquire("conversation-a", turn=True):
            pass  # a running turn: the switch waits
        await t.start()
        assert t.mode == "waiting"
        await t.hand_back()  # he changes his mind (or Stop) while it waits
        assert t.mode == "assistant" and not service.driving
        service.end_turn("conversation-a")
        await asyncio.sleep(0.5)
        assert spawned == [] and t.mode == "assistant" and not pending(tmp_path)
        await service.stop()

    asyncio.run(main())


def test_s1_s2_more_permissions_and_downloads_blocked(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    seed_takeover_preferences(root)
    prefs = json.loads((root / "Default" / "Preferences").read_text())
    blocked = prefs["profile"]["default_content_setting_values"]
    for kind in ("bluetooth_guard", "idle_detection", "window_placement", "local_fonts",
                 "automatic_downloads", "payment_handler", "storage_access"):  # fmt: skip
        assert blocked[kind] == 2, kind
    assert prefs["download_restrictions"] == BLOCK_ALL_DOWNLOADS


def test_k4b_hand_back_while_waiting_ends_the_headless_chrome_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's probe: the profile is in use while the headless Chrome runs (as
    real Chrome's lock makes it). Hand-back during `waiting` must not stick in
    `restoring`."""
    import sasha.browser.profile as profile_module

    async def main() -> None:
        service, t, spawned = _rig(tmp_path, monkeypatch)
        monkeypatch.setattr(profile_module, "in_use", lambda root: service.chrome is not None)
        async with service.acquire("conversation-a", turn=True):
            pass  # a turn ran: the headless Chrome is up and holds the profile
        assert service.chrome is not None
        await t.start()
        assert t.mode == "waiting"
        await t.hand_back()
        assert t.mode == "assistant" and not service.driving
        assert service.chrome is None and not pending(tmp_path) and spawned == []
        service.end_turn("conversation-a")
        await service.stop()

    asyncio.run(main())


def test_the_panel_says_why_after_three_failed_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(takeover_module, "RETRY_S", 0.02)

    async def never(data_dir: Path) -> str:
        raise cleanup_module.RestoreIncomplete("The browser profile is still in use")

    monkeypatch.setattr(takeover_module, "finish_takeover", never)

    async def main() -> None:
        service, t, spawned = _rig(tmp_path, monkeypatch)
        await t.start()
        await _until(lambda: t.mode == "owner")
        spawned[0][2].closed.set()
        await _until(lambda: t.mode == "restoring")
        assert t.state()["restore_problem"] is None  # not after one try
        await _until(lambda: t.state()["restore_problem"] is not None)
        assert t.state()["restore_problem"] == "The browser profile is still in use"
        assert t.mode == "restoring" and service.driving
        if t._retry is not None:
            t._retry.cancel()
        await service.stop()

    asyncio.run(main())
