"""The app window after a brain restart: it re-reads the token and reloads (found on
the live machine: the old token was refused with 403 again and again)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sasha.app import launcher
from sasha.app.token_watch import TokenWatch


def test_a_new_brain_means_a_new_token_once() -> None:
    file = {"token": "a" * 64}
    watch = TokenWatch(lambda: file["token"])
    assert watch.take() == "a" * 64  # the page is loaded with it
    assert watch.check() is None  # same brain: nothing to do
    file["token"] = None  # type: ignore[assignment]  # the brain is down: wait
    assert watch.check() is None
    file["token"] = "b" * 64  # the new brain wrote its own
    assert watch.check() == "b" * 64
    assert watch.check() is None  # reloaded once, not again and again


@pytest.mark.skipif(not launcher.available(), reason="the `app` extra (PySide6) is not installed")
def test_the_real_shell_reloads_with_the_new_token(tmp_path: Path) -> None:
    code = """
import json, pathlib, sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import sasha.app.shell as shell_module
from sasha.app.shell import build, reload_on_new_token, start_url
from sasha.app.token_watch import TokenWatch
shell_module.WATCH_MS = 100
app = QApplication([])
file = {"t": "a" * 64}
watch = TokenWatch(lambda: file["t"])
shell = build(app, 9, pathlib.Path(sys.argv[1]), "t", None, token=watch.take())
loads = []
real_load = shell.page.load
def load(url):
    loads.append(url.toString())
    real_load(url)
shell.page.load = load
reload_on_new_token(shell, watch, lambda t: start_url(9, "/", t))
def restart():
    file["t"] = "b" * 64  # the brain restarted and wrote a new token
def done():
    print("RESULT " + json.dumps({"loads": loads}), flush=True)
    app.quit()
QTimer.singleShot(300, restart)
QTimer.singleShot(3000, done)
app.exec()
"""
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
    }
    done = subprocess.run([sys.executable, "-c", code, str(tmp_path / "p")], env=env,
                          capture_output=True, text=True, timeout=60)  # fmt: skip
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, done.stderr[-2000:]
    r = json.loads(line[len("RESULT ") :])
    # once, with the new token, as a new document (about:blank first: review of the live
    # 403s, a fragment-only load kept the page's old token)
    assert r["loads"] == ["about:blank", "http://127.0.0.1:9/#t=" + "b" * 64]
