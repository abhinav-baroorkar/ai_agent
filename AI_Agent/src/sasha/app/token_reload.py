"""The app window takes a new brain's token (split from shell.py; found live 2026-11-16).

Every WATCH_MS the shell re-reads the brain's token file (token_watch.py). On a new
token the page is opened again with it:
- through about:blank first: loading `/#t=<new>` over the open page only changed its
  fragment (a same-document navigation), so the page's script never ran again and
  kept the old token (every /ws/browser was refused);
- and again after RETRY_MS if that load failed (review S1 of window-token-reload): the
  brain writes its token before it listens, so a tick in that gap met a closed port,
  showed an error page and never loaded again. It retries until the page loads, a
  newer token comes, or MAX_RETRIES (two minutes) have passed.
`shell.loaded` is cleared at the blank step, so a wake meanwhile waits for the new
page and still plays the orb's entrance there (review nit).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sasha.app.token_watch import WATCH_MS, TokenWatch

BLANK = "about:blank"
RETRY_MS = 1000
MAX_RETRIES = 120


def reload_on_new_token(shell: Any, watch: TokenWatch, url: Callable[[str], str]) -> None:
    from PySide6.QtCore import QTimer, QUrl

    timer = QTimer(shell.window)
    timer.setInterval(WATCH_MS)
    # target: the address with the newest token; step: blank, page, or waiting (retry)
    state: dict[str, Any] = {"target": None, "step": None, "tries": 0}

    def blank_step() -> None:
        shell.loaded = False  # a wake now waits for the new page (the nit)
        state["step"] = "blank"
        shell.page.load(QUrl(BLANK))

    def retry(target: str) -> None:
        if state["target"] == target and state["step"] == "waiting":
            blank_step()  # through about:blank again: always a new document

    def finished(ok: bool) -> None:
        target = state["target"]
        if target is None:
            return
        if state["step"] == "blank":
            if shell.page.url().toString() == BLANK:
                state["step"] = "page"
                shell.page.load(QUrl(target))
            return
        if state["step"] != "page":
            return
        if ok:
            state.update(target=None, step=None, tries=0)  # the page has the token
        elif state["tries"] >= MAX_RETRIES:
            state.update(target=None, step=None)  # give up; a new token starts again
        else:  # e.g. the new brain isn't listening yet: try again in a moment
            state["tries"] += 1
            state["step"] = "waiting"
            QTimer.singleShot(RETRY_MS, lambda: retry(target))

    def tick() -> None:
        if shell.stack is not None and shell.stack.currentIndex() == 0:
            return  # --attach still waiting: it reads the token when the brain answers
        new = watch.check()
        if new is not None:
            state.update(target=url(new), tries=0)  # the newest token wins
            blank_step()

    shell.page.loadFinished.connect(finished)
    timer.timeout.connect(tick)
    timer.start()
