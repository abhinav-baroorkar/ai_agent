"""The app shell: one Qt WebEngine window showing the brain's own web UI.

Run as `python -m sasha.app.shell --port <brain port> --dead-proxy <port>` (the
launcher does this). Network: a request interceptor allows only the brain's address
(`policy.request_allowed`), and Chromium itself gets a dead proxy and the WebRTC policy,
as the Chrome window does. Profile: `Data/app-profile/`, cache in memory, no cookies kept,
permissions in memory and granted in code (the microphone, our origin only). One window:
a Unix socket in $XDG_RUNTIME_DIR takes `raise` and `wake` (`single.py`).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sasha.app import policy
from sasha.app.exit_note import HANDOVER, ExitNotes
from sasha.app.single import NoRuntimeDir, notify, socket_path
from sasha.app.token_watch import WATCH_MS, TokenWatch
from sasha.app.waiting import BrainWait, brain_up
from sasha.server.window_token import read_token

log = logging.getLogger(__name__)

CHROMIUM_FLAGS = (
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--dns-prefetch-disable",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--no-pings",
)


# Home plays the entrance on this event; `window.assistantWake` is left for a page still
# starting (Home reads it when it is ready), so a cold start shows the entrance too.
def wake_js(just_shown: bool) -> str:
    """Tell the page a wake came. `entrance`: play the full entrance, because the window
    was just shown (cold start, or brought up from hidden or behind) or Home wasn't the
    screen in view; otherwise the orb only brightens (design review)."""
    shown = "true" if just_shown else "false"
    return (
        "(() => { const wasHome = (location.hash || '#home') === '#home';"
        f" const entrance = {shown} || !wasHome;"
        " window.assistantWake = { at: performance.now(), entrance };"
        " location.hash = 'home';"
        " window.dispatchEvent(new CustomEvent('assistant-wake', { detail: { entrance } }));"
        " })();"
    )


def chromium_flags(dead_proxy: int, port: int) -> str:
    """Everything goes to a dead proxy, loopback included (`<-loopback>` removes
    Chromium's built-in loopback bypass), except the brain's own address: so even
    without the interceptor no other local service is reachable."""
    return " ".join(
        (
            *CHROMIUM_FLAGS,
            f"--proxy-server=http://127.0.0.1:{dead_proxy}",
            f"--proxy-bypass-list=<-loopback>;127.0.0.1:{port}",  # this order: measured
        )
    )


# Environment variables that would open a DevTools port (or inspector) into the window
# that holds the microphone grant; removed before Qt starts (review A2).
DEBUG_VARIABLES = ("QTWEBENGINE_REMOTE_DEBUGGING", "QTWEBENGINE_INSPECTOR_SERVER")


def harden_environment(env: Any, dead_proxy: int, port: int) -> None:
    for name in DEBUG_VARIABLES:
        env.pop(name, None)
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = chromium_flags(dead_proxy, port)  # ours only


@dataclass
class Shell:
    window: Any
    page: Any
    profile: Any
    interceptor: Any
    loaded: bool = False
    pending_wake: bool = False
    # --attach: a plain line in front until the brain is up (app/waiting.py)
    stack: Any = None
    label: Any = None
    url: Any = None


def start_url(port: int, start: str = "/", token: str | None = None) -> str:
    """The page the shell loads. With the brain's token, as `#t=<token>`: the fragment
    never reaches the server or a log, and the page strips it from the address at once
    (take-over 9.0 C4). The shell reads the token from the brain's private file itself,
    so it is never on a command line (the Chrome window needs the start page for that)."""
    url = policy.own_origin(port) + (start if start.startswith("/") else "/")
    return f"{url}#t={token}" if token else url


def build(
    app: Any,
    port: int,
    profile_dir: Path,
    title: str,
    icon: Path | None,
    start: str = "/",
    intercept: bool = True,  # False only for the probe that shows the dead proxy alone
    token: str | None = None,
    waiting: str | None = None,  # --attach: show this and load nothing yet
) -> Shell:
    """The window, its page and profile. Qt imports are here, so the module imports
    without PySide6 (the launcher checks for it first)."""
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import QIcon
    from PySide6.QtWebEngineCore import (
        QWebEnginePage,
        QWebEngineProfile,
        QWebEngineSettings,
        QWebEngineUrlRequestInterceptor,
    )
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QLabel, QMainWindow, QStackedWidget

    class Interceptor(QWebEngineUrlRequestInterceptor):
        blocked = 0

        def interceptRequest(self, info: Any) -> None:
            if not policy.request_allowed(info.requestUrl().toString(), port):
                Interceptor.blocked += 1
                info.block(True)  # counted only: never logged with the address

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url: Any, kind: Any, main: bool) -> bool:
            return policy.navigation_allowed(url.toString(), port)

        def createWindow(self, kind: Any) -> Any:
            return None

    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(profile_dir, 0o700)  # also an existing folder made with a looser mode
    profile = QWebEngineProfile("assistant-window", app)
    profile.setPersistentStoragePath(str(profile_dir))
    profile.setCachePath(str(profile_dir / "cache"))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )
    if hasattr(QWebEngineProfile, "PersistentPermissionsPolicy"):  # Qt 6.8+
        profile.setPersistentPermissionsPolicy(
            QWebEngineProfile.PersistentPermissionsPolicy.StoreInMemory
        )
    interceptor = Interceptor(profile)
    if intercept:
        profile.setUrlRequestInterceptor(interceptor)

    page = Page(profile, app)
    settings = page.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)

    def on_permission(permission: Any) -> None:
        origin = permission.origin().toString()
        kind = permission.permissionType().name
        if policy.permission_allowed(origin, kind, port):
            permission.grant()
        else:
            permission.deny()

    page.permissionRequested.connect(on_permission)

    window = QMainWindow()
    view = QWebEngineView(window)
    view.setPage(page)
    window.setWindowTitle(title)
    if icon is not None and icon.is_file():
        window.setWindowIcon(QIcon(str(icon)))
    window.resize(1100, 800)
    shell = Shell(window, page, profile, interceptor)

    def loaded(ok: bool) -> None:
        if not ok:  # e.g. a refused navigation: the page we have is still there
            return
        shell.loaded = True
        if shell.pending_wake:
            shell.pending_wake = False
            page.runJavaScript(wake_js(True))  # a cold start: always just shown

    page.loadFinished.connect(loaded)
    url = QUrl(start_url(port, start, token))
    if waiting is None:
        window.setCentralWidget(view)
        page.load(url)
        return shell
    label = QLabel(waiting)  # plain text only: never markup
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    label.setStyleSheet("background:#0b0f14; color:#cfd8e3; font-size:20px; padding:24px;")
    stack = QStackedWidget(window)
    stack.addWidget(label)
    stack.addWidget(view)
    window.setCentralWidget(stack)
    shell.stack, shell.label, shell.url = stack, label, url
    return shell


def load_when_up(
    shell: Shell, wait: BrainWait, late: str, url: Callable[[], str] | None = None
) -> None:
    """--attach: show the page once the brain answers; say so if it is late. `url` is
    built only then (with the token the brain has just written)."""
    from PySide6.QtCore import QTimer, QUrl

    timer = QTimer(shell.window)
    timer.setInterval(250)

    def tick() -> None:
        state = wait.state()
        if state == "up":
            timer.stop()
            shell.stack.setCurrentIndex(1)
            shell.page.load(QUrl(url()) if url is not None else shell.url)
        elif state == "late" and shell.label.text() != late:
            shell.label.setText(late)

    timer.timeout.connect(tick)
    wait.start()
    timer.start()


def bring_up(app: Any, shell: Shell, wake: bool, notice: str) -> None:
    """Show and raise; on a wake, switch to Home with the orb listening. If the desktop
    keeps focus elsewhere (GNOME's focus-stealing prevention, Wayland), a notification
    whose click opens the app (through its menu entry) is the fallback."""
    from PySide6.QtCore import QTimer

    win = shell.window
    # In view and in front already? Then the orb only brightens; otherwise it enters.
    just_shown = not win.isVisible() or win.isMinimized() or not win.isActiveWindow()
    win.showNormal()
    win.raise_()
    win.activateWindow()
    if wake:
        if shell.loaded:
            shell.page.runJavaScript(wake_js(just_shown))
        else:  # a cold start: once the page is there
            shell.pending_wake = True

        def check() -> None:
            if app.activeWindow() is None:
                notify(notice)

        QTimer.singleShot(400, check)


QUIET_AFTER_MS = 5000  # start-up output goes to the start log (app/start_check.py)


def _quiet_after_start() -> None:
    """Once the window is up, later output no longer lands in the start log."""
    from PySide6.QtCore import QTimer

    from sasha.app.start_check import quiet_stderr

    QTimer.singleShot(QUIET_AFTER_MS, quiet_stderr)


def reload_on_new_token(shell: Shell, watch: TokenWatch, url: Callable[[str], str]) -> None:
    """Every WATCH_MS: if the brain has a new token, load the page again with it."""
    from PySide6.QtCore import QTimer, QUrl

    timer = QTimer(shell.window)
    timer.setInterval(WATCH_MS)

    def tick() -> None:
        if shell.stack is not None and shell.stack.currentIndex() == 0:
            return  # --attach still waiting: it reads the token when the brain answers
        new = watch.check()
        if new is not None:
            shell.page.load(QUrl(url(new)))

    timer.timeout.connect(tick)
    timer.start()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sasha.app.shell")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--dead-proxy", type=int, required=True)
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--title", default="Assistant")
    p.add_argument("--notice", default="Listening")
    p.add_argument("--icon", type=Path, default=None)
    p.add_argument("--wake", action="store_true", help="started by the wake word")
    p.add_argument("--start-path", default="/", help="page to open (the visible check)")
    p.add_argument("--state-dir", type=Path, default=None, help="where the brain's token is")
    p.add_argument("--wait-for-brain", type=float, default=None, help="--attach: seconds")
    p.add_argument("--starting-notice", default="Starting…")
    p.add_argument("--late-notice", default="The assistant didn't start.")
    a = p.parse_args(argv)
    from sasha.app.single import SingleInstance

    notes = ExitNotes()  # every way out leaves a line in the start log (app/exit_note.py)
    notes.catch_crashes()
    try:
        single = SingleInstance(socket_path())
    except NoRuntimeDir as exc:  # never a socket in a shared folder such as /tmp
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    if single.hand_over("wake" if a.wake else "raise"):
        notes.write(HANDOVER)
        return 0  # the window is already open: it raised itself
    harden_environment(os.environ, a.dead_proxy, a.port)
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setApplicationName("assistant-window")  # the window class, for the menu entry
    app.setDesktopFileName("assistant-window")
    notes.watch(app)
    attach = a.wait_for_brain is not None
    waiting = a.starting_notice if attach else None

    def token() -> str | None:  # None: the browser panel is refused
        return read_token(a.state_dir) if a.state_dir else None

    watch = TokenWatch(token)
    shell = build(
        app,
        a.port,
        a.profile,
        a.title,
        a.icon,
        a.start_path,
        token=None if attach else watch.take(),
        waiting=waiting,
    )
    if attach:  # never starts a brain: waits for the service (app/waiting.py)
        wait = BrainWait(lambda: brain_up(a.port), wait_s=a.wait_for_brain)
        # the token is read once the brain answers: the file of a previous run is stale
        load_when_up(
            shell, wait, a.late_notice, lambda: start_url(a.port, a.start_path, watch.take())
        )
    single.serve(app, lambda command: bring_up(app, shell, command == "wake", a.notice))
    shell.window.show()
    _quiet_after_start()
    if a.state_dir is not None:  # a restarted brain: reload with its new token
        reload_on_new_token(shell, watch, lambda t: start_url(a.port, a.start_path, t))
    if a.wake:
        bring_up(app, shell, True, a.notice)
    code = int(app.exec())
    single.close()
    tear_down(shell)
    notes.ended(code)
    return code


def tear_down(shell: Shell) -> None:
    """The window, then the page, then its profile. Left to Python's exit, the profile
    went first and the shell segfaulted after a clean quit (libQt6Quick, 2026-11-16)."""
    import shiboken6

    for obj in (shell.window, shell.page, shell.profile):
        if obj is not None and shiboken6.isValid(obj):
            shiboken6.delete(obj)


if __name__ == "__main__":
    raise SystemExit(main())
