"""Screencast details: restart after navigation, the 5-a-second cap, preferences seed."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import sasha.browser.screencast as screencast
from sasha.browser.profile import prepare_profile, seed_preferences
from sasha.browser.screencast import Screencast
from tests.test_browser_view import JPEG, FakePipe


def test_a_navigation_restarts_the_screencast() -> None:
    async def main() -> None:
        pipe = FakePipe()
        sent: list[dict[str, Any]] = []

        async def emit(m: dict[str, Any]) -> None:
            sent.append(m)

        cast = Screencast(pipe, "t1", 1, emit)  # type: ignore[arg-type]
        await cast.start()
        assert pipe.named("Page.enable")  # needed for frameNavigated
        pipe.emit(
            "Page.frameNavigated", {"frame": {"id": "f", "parentId": "p"}, "_session": "view-1"}
        )
        pipe.emit("Page.frameNavigated", {"frame": {"id": "f"}, "_session": "other"})
        await asyncio.sleep(0.01)
        assert len(pipe.named("Page.startScreencast")) == 1  # iframes and others: no
        pipe.emit("Page.frameNavigated", {"frame": {"id": "main"}, "_session": "view-1"})
        await asyncio.sleep(0.01)
        assert len(pipe.named("Page.startScreencast")) == 2 and cast.restarts == 1
        await cast.stop()

    asyncio.run(main())


def test_frames_are_capped_at_about_five_a_second(monkeypatch: Any) -> None:
    monkeypatch.setattr(screencast, "MIN_INTERVAL_S", 0.2)

    async def main() -> None:
        pipe = FakePipe()
        sent: list[dict[str, Any]] = []
        cast: Screencast | None = None

        async def emit(m: dict[str, Any]) -> None:
            sent.append(m)
            assert cast is not None
            asyncio.get_running_loop().create_task(cast.window_ack(m["seq"]))  # a fast window

        cast = Screencast(pipe, "t1", 1, emit)  # type: ignore[arg-type]
        await cast.start()
        for n in range(50):  # Chrome at 50 frames a second, for one second
            pipe.emit("Page.screencastFrame", {"data": JPEG, "sessionId": n, "_session": "view-1"})
            await asyncio.sleep(0.02)
        await cast.stop()
        assert 4 <= len(sent) <= 6 and cast.dropped >= 44

    asyncio.run(main())


def test_preferences_turn_off_prediction_and_never_follow_links(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path / "a")
    seed_preferences(root)
    prefs = json.loads((root / "Default" / "Preferences").read_text())
    assert prefs["net"]["network_prediction_options"] == 2
    (root / "Default" / "Preferences").write_text(json.dumps({"net": {"x": 1}, "keep": True}))
    seed_preferences(root)
    prefs = json.loads((root / "Default" / "Preferences").read_text())
    assert prefs == {"net": {"x": 1, "network_prediction_options": 2}, "keep": True}
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (root / "Default" / "Preferences").unlink()
    os.symlink(outside, root / "Default" / "Preferences")
    seed_preferences(root)  # the link is replaced, not written through
    assert outside.read_text() == "{}"
    assert not (root / "Default" / "Preferences").is_symlink()


def test_guard_counts_only_the_pages_own_refusals_as_refused() -> None:
    import time

    from sasha.browser.proxy import SETTLE_S, FilteringProxy
    from sasha.server.browser_view import _guard_counts

    proxy = FilteringProxy()
    proxy.stats.allowed, proxy.stats.background_refused = 4, 2  # 2 refused by name
    old = time.monotonic() - SETTLE_S - 1
    for host, category in (
        ("127.0.0.1", "loopback"),
        ("router.lan", "local_name"),
        ("www.google.com", "unresolvable"),
    ):
        proxy.unsettled.append((old, host, category))
    proxy.unsettled.append((time.monotonic(), "late.example", "private"))  # too recent
    page_hosts = {"127.0.0.1", "router.lan", "news.example"}
    counts = _guard_counts(proxy, page_hosts)
    assert counts == {
        "allowed": 4,
        "refused": {"loopback": 1, "local_name": 1},  # the page asked for these
        "background_refused": 3,  # Chrome's own: 2 by name + 1 nobody's page asked for
    }
    assert len(proxy.unsettled) == 1  # counted once it has settled
    # running totals: they keep growing past any ring size, and a host a page asks for
    # later doesn't re-label a refusal already counted
    for _ in range(3000):
        proxy.unsettled.append((old, "www.google.com", "unresolvable"))
    page_hosts.add("www.google.com")
    proxy.unsettled.popleft()  # drop the recent one for this check
    counts = _guard_counts(proxy, page_hosts)
    assert counts["background_refused"] == 3 and counts["refused"]["unresolvable"] == 3000


def test_preferences_errors_never_stop_the_browser(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    (root / "Default" / "Preferences").mkdir(parents=True)  # a folder in the way
    seed_preferences(root)  # logged, no exception
    assert (root / "Default" / "Preferences").is_dir()


def test_a_navigation_loop_cannot_get_around_the_cap(monkeypatch: Any) -> None:
    """Review W2: a page navigating 50 times a second, a frame after each navigation."""
    monkeypatch.setattr(screencast, "MIN_INTERVAL_S", 0.2)

    async def main() -> None:
        pipe = FakePipe()
        sent: list[dict[str, Any]] = []
        cast: Screencast | None = None

        async def emit(m: dict[str, Any]) -> None:
            sent.append(m)
            assert cast is not None
            asyncio.get_running_loop().create_task(cast.window_ack(m["seq"]))

        cast = Screencast(pipe, "t1", 1, emit)  # type: ignore[arg-type]
        await cast.start()
        for n in range(50):
            pipe.emit("Page.frameNavigated", {"frame": {"id": "main"}, "_session": "view-1"})
            await asyncio.sleep(0.005)
            pipe.emit("Page.screencastFrame", {"data": JPEG, "sessionId": n, "_session": "view-1"})
            await asyncio.sleep(0.015)
        await cast.stop()
        assert 4 <= len(sent) <= 6, len(sent)
        starts = len(pipe.named("Page.startScreencast"))
        assert starts <= 4, starts  # coalesced: about two restarts a second, plus the first

    asyncio.run(main())
