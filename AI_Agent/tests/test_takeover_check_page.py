"""Take-over must show the owner the person check he is asked to solve (found live,
2026-11-16: the window opened the engine's home page and the check was gone), and the
line must say where Take over is for the bot he is in. Fakes only: no window."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

from sasha.browser import tools as tools_module
from sasha.browser.pages import (
    NOT_RESULTS,
    NOT_RESULTS_HERE,
    PERSON_CHECK,
    PERSON_CHECK_HERE,
    PageError,
)
from sasha.browser.profile import prepare_profile
from sasha.browser.takeover_files import (
    CHECK_PAGE,
    TAKEOVER_WIPE,
    start_page,
    wipe_takeover_files,
)
from sasha.browser.tools import BrowserConversation, open_url_refusal
from sasha.config import Settings
from sasha.conversation import current_turn, new_turn
from sasha.prompting import prompt_lines
from sasha.server.browse_truth import BrowseTruth
from sasha.tools import ToolError
from tests.test_browser_takeover import TEMPLATE, _rig, _until
from tests.test_browser_wiring import FakeTabs, conv  # noqa: F401  (the fixture)

BLOCKED = "https://html.duckduckgo.com/html/?q=India+wikipedia"


def test_the_window_opens_the_blocked_search_through_a_private_page(tmp_path: Path) -> None:
    root = prepare_profile(tmp_path)
    page = start_page(root, TEMPLATE, BLOCKED)
    path = Path(unquote(urlsplit(page).path))
    assert page.startswith("file://") and path == root / CHECK_PAGE
    assert "India" not in page  # C2: no query on the command line, only the file's path
    assert path.stat().st_mode & 0o777 == 0o600 and BLOCKED in path.read_text()
    assert CHECK_PAGE in TAKEOVER_WIPE
    wipe_takeover_files(root)
    assert not path.exists()  # gone at hand-back, query and all


@pytest.mark.parametrize(
    "blocked",
    [None, "https://evil.example/?q=x", "http://html.duckduckgo.com/html/?q=x", "not a url"],
)
def test_anything_else_opens_the_home_page(tmp_path: Path, blocked: str | None) -> None:
    root = prepare_profile(tmp_path)
    assert start_page(root, TEMPLATE, blocked) == "https://html.duckduckgo.com/"
    assert not (root / CHECK_PAGE).exists()


def test_take_over_opens_the_check_then_forgets_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def main() -> None:
        service, t, spawned = _rig(tmp_path, monkeypatch)
        service.person_check_met(BLOCKED)
        await t.start()
        await _until(lambda: t.mode == "owner")
        argv = spawned[0][0]
        assert argv[-1].startswith("file://") and argv[-1].endswith(CHECK_PAGE)
        assert not any("India" in a for a in argv)
        spawned[0][2].closed.set()
        await _until(lambda: t.mode == "assistant")
        assert service.blocked_search() is None  # a new check sets it again
        assert not (prepare_profile(tmp_path) / CHECK_PAGE).exists()
        await t.start()  # no check waiting: the home page, as before
        await _until(lambda: t.mode == "owner")
        assert spawned[1][0][-1] == "https://html.duckduckgo.com/"
        spawned[1][2].closed.set()
        await _until(lambda: t.mode == "assistant")
        await service.stop()

    asyncio.run(main())


def test_an_old_check_is_not_reopened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = _rig(tmp_path, monkeypatch)
    now = [100.0]
    service.clock = lambda: now[0]
    service.person_check_met(BLOCKED)
    assert service.blocked_search() == BLOCKED
    now[0] += 1801
    assert service.blocked_search() is None


class CheckTabs(FakeTabs):
    message = PERSON_CHECK

    async def search_results(self, url: str) -> dict[str, Any]:
        raise PageError(CheckTabs.message)


def _search(
    talk: BrowserConversation, monkeypatch: pytest.MonkeyPatch
) -> tuple[ToolError, list[str]]:
    monkeypatch.setattr(tools_module, "BrowserTabs", CheckTabs)
    met: list[str] = []
    monkeypatch.setattr(
        type(talk.bridge.service), "person_check_met", lambda s, u: met.append(u), raising=False
    )
    token = current_turn.set(new_turn(("Search for India on Wikipedia on your browser",), "text"))
    try:
        with pytest.raises(ToolError) as caught:
            talk.search("India Wikipedia")
    finally:
        current_turn.reset(token)
    return caught.value, met


def test_a_direct_browser_bot_is_told_take_over_is_beside_its_chat(
    conv: BrowserConversation,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err, met = _search(conv, monkeypatch)
    assert str(err) == PERSON_CHECK_HERE and err.asks == "person_check"
    assert "main assistant" not in str(err)
    assert met and met[0].startswith("https://search.example/?q=")  # the server's own URL
    assert conv.person_check


def test_the_helper_keeps_the_line_to_the_main_chat(
    conv: BrowserConversation,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv.helper_task = "India wikipedia"
    monkeypatch.setattr(tools_module, "check_search_words", lambda *a: None)
    err, _ = _search(conv, monkeypatch)
    assert str(err) == PERSON_CHECK


def test_no_results_points_beside_the_chat_too(
    conv: BrowserConversation,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(CheckTabs, "message", NOT_RESULTS)
    err, met = _search(conv, monkeypatch)
    assert str(err) == NOT_RESULTS_HERE and not met


def test_open_url_says_why_in_plain_words() -> None:
    owner = ["Search for India on Wikipedia on your browser"]
    named = open_url_refusal("https://en.wikipedia.org/wiki/India", "not_from_owner", owner)
    assert "named wikipedia but didn't write its address" in named
    other = open_url_refusal("https://evil.example/steal", "not_from_owner", owner)
    assert "isn't one of them" in other and "named" not in other
    assert "isn't a web address" in open_url_refusal("India", "not_a_web_address", owner)


def test_the_server_says_the_line_not_the_models_rewording(settings: Settings) -> None:
    truth = BrowseTruth(prompt_lines(settings), "Search for India on Wikipedia")
    model = "Open Your bots, then the main assistant's chat, and press Take over."
    failed = [
        {"name": "search", "ok": False, "asks": "person_check"},
        {"name": "open_url", "ok": False},
        {"name": "search", "ok": False, "asks": "person_check"},
    ]
    assert truth.finish(model, failed)[0] == PERSON_CHECK_HERE
    worked = [*failed, {"name": "read_page", "ok": True}]
    assert truth.finish(model, worked)[0] == model  # a browser step worked: left alone
    other = [{"name": "open_url", "ok": False}]
    assert truth.finish(model, other)[0] == model  # no person check: left alone


def test_the_window_and_the_headless_browser_share_one_password_store() -> None:
    """Cookies are encrypted with the password store's key: a window on another store
    (the desktop keyring) would leave a pass the headless browser can't read (measured
    with real Chrome, 2026-11-16: under the same store the cookie came through)."""
    from sasha.browser.takeover_files import visible_flags
    from sasha.server.launch import ISOLATION_FLAGS

    store = [f for f in ISOLATION_FLAGS if f.startswith("--password-store=")]
    assert store == ["--password-store=basic"]
    assert store[0] in visible_flags(Path("/profile"), 1, "about:blank")


def test_the_state_says_a_check_is_waiting_never_which(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, t, _ = _rig(tmp_path, monkeypatch)
    assert t.state()["person_check"] is False
    service.person_check_met(BLOCKED)
    state = t.state()
    assert state["person_check"] is True and "India" not in str(state)
