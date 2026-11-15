"""Page reader, backstop counts and the one-browser service (no Chrome needed)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sasha.browser.backstop import LogOnlyBackstop
from sasha.browser.reader import MAX_ELEMENTS, PART_CHARS, clean, read_tree
from sasha.browser.service import BrowserBusyError, BrowserService


def node(
    nid: int, role: str, name: str = "", children: tuple[int, ...] = (), **kw: Any
) -> dict[str, Any]:
    return {
        "nodeId": str(nid),
        "role": {"value": role},
        "name": {"value": name},
        "childIds": [str(c) for c in children],
        "backendDOMNodeId": 100 + nid,
        **kw,
    }


def test_numbered_elements_headings_and_text() -> None:
    nodes = [
        node(1, "RootWebArea", "Pune", (2, 3, 4, 5, 7)),
        node(2, "heading", "Pune"),
        node(3, "StaticText", "Pune is a city."),
        node(4, "link", "Next page"),
        node(5, "button", "Play", (6,)),
        node(6, "StaticText", "Play"),  # the button's own label: not repeated
        node(7, "generic", "", (8,), ignored=True),
        node(8, "textbox", "Search"),
    ]
    view = read_tree(nodes, title="Pune")
    text = view.part(1)
    assert text.splitlines() == [
        "(part 1 of 1)",
        "Page: Pune",
        "## Pune",
        "Pune is a city.",
        '[1] link "Next page"',
        '[2] button "Play"',
        '[3] textbox "Search"',
    ]
    assert view.elements[2].backend_node_id == 105 and view.elements[3].role == "textbox"


def test_control_and_invisible_characters_are_stripped() -> None:
    sneaky = "Click‮ here​ now\x00\x1b[31m"
    assert clean(sneaky) == "Click here now [31m"
    assert len(clean("x" * 500)) == 160


def test_long_pages_are_split_and_elements_capped() -> None:
    texts = [node(i, "StaticText", "word " * 60) for i in range(2, 200)]
    links = [node(1000 + i, "link", f"link {i}") for i in range(MAX_ELEMENTS + 10)]
    root = node(1, "RootWebArea", "", tuple(int(n["nodeId"]) for n in texts + links))
    view = read_tree([root, *texts, *links])
    assert len(view.parts) > 1 and all(len(p) <= PART_CHARS + 1000 for p in view.parts)
    assert len(view.elements) == MAX_ELEMENTS and view.truncated
    words = sum(p.count("word") for p in view.parts)
    assert words == 198 * 60  # no page text was dropped, only split into parts
    assert "no part 99" in view.part(99)


def test_backstop_counts_methods_only() -> None:
    now = [100.0]
    bs = LogOnlyBackstop(clock=lambda: now[0])
    bs.request("POST")  # page script on its own
    bs.click()
    now[0] += 1.0
    bs.on_request_will_be_sent({"request": {"method": "post", "url": "https://secret.example/x"}})
    now[0] += 5.0
    bs.request("PUT")  # long after the click
    bs.request("GET")
    c = bs.counts
    assert (c.clicks, c.unsafe_after_click, c.unsafe_without_click) == (1, 1, 2)
    assert c.requests == {"POST": 2, "PUT": 1, "GET": 1}
    assert "secret" not in repr(c)


class FakeChrome:
    def __init__(self) -> None:
        self.stopped = False

        class Proc:
            returncode: int | None = None

        self.process = Proc()

    async def stop(self) -> None:
        self.stopped = True


def test_service_one_bot_at_a_time_and_idle_stop(tmp_path: Path) -> None:
    started: list[FakeChrome] = []

    async def starter(data_dir: Path, port: int) -> Any:
        started.append(FakeChrome())
        return started[-1]

    async def main() -> None:
        svc = BrowserService(tmp_path, idle_s=0.05, starter=starter)
        async with svc.acquire("bot-a"):
            with pytest.raises(BrowserBusyError):
                async with svc.acquire("bot-b"):
                    pass
        async with svc.acquire("bot-b"):
            pass
        assert len(started) == 1  # reused, started lazily once
        await asyncio.sleep(0.2)
        assert started[0].stopped and svc.chrome is None and svc.proxy is None
        async with svc.acquire("bot-a"):
            assert len(started) == 2  # started again after the idle stop
        await svc.stop()
        assert started[1].stopped

    asyncio.run(main())


def test_service_start_failure_leaves_nothing_running(tmp_path: Path) -> None:
    async def starter(data_dir: Path, port: int) -> Any:
        raise RuntimeError("no chrome")

    async def main() -> None:
        svc = BrowserService(tmp_path, starter=starter)
        with pytest.raises(RuntimeError):
            async with svc.acquire("bot"):
                pass
        assert svc.proxy is None and svc.chrome is None and svc.holder is None

    asyncio.run(main())
