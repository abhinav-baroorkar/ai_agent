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
    from sasha.browser.proxy import FilteringProxy
    from sasha.server.browser_view import _guard_counts

    proxy = FilteringProxy()
    proxy.stats.allowed, proxy.stats.background_refused = 4, 2  # 2 refused by name
    proxy.refusals.extend(
        [
            ("127.0.0.1", "loopback"),
            ("router.lan", "local_name"),
            ("www.google.com", "unresolvable"),
        ]
    )
    counts = _guard_counts(proxy, page_hosts={"127.0.0.1", "router.lan", "news.example"})
    assert counts == {
        "allowed": 4,
        "refused": {"loopback": 1, "local_name": 1},  # the page asked for these
        "background_refused": 3,  # Chrome's own: 2 by name + 1 nobody's page asked for
    }
