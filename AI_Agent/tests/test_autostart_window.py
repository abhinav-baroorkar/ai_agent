"""The window at login: the autostart entry, `sasha window --attach`, and waiting for
the brain. A fake HOME and a fake systemctl only; the real files are never touched."""

from __future__ import annotations

import http.server
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sasha.app import autostart_entry, launcher, waiting
from sasha.app.autostart_entry import MARKER, entry_path
from sasha.app.waiting import BrainWait, brain_up
from sasha.cli import build_parser
from sasha.config import Settings
from sasha.listener.autostart import BRAIN, LISTEN, UNDO, autostart
from sasha.server import launch

HAS_QT = launcher.available()  # PySide6 with Qt WebEngine (a namespace leftover is not enough)


def _root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "proj"
    python = root / "Dependencies" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    return root, python, tmp_path / "home"


def _on(settings: Settings, tmp_path: Path, yes: bool = True) -> tuple[int, list[Any], str]:
    root, python, home = _root(tmp_path)
    ran: list[Any] = []
    out: list[str] = []
    rc = autostart(
        "on", root, yes=yes, python=python, home=home,
        run=lambda c: ran.append(list(c)) or 0, out=out.append, settings=settings,
    )  # fmt: skip
    return rc, ran, "\n".join(out)


# --- the entry ------------------------------------------------------------------------
def test_the_entry_opens_the_window_in_attach_mode(settings: Settings, tmp_path: Path) -> None:
    root, python, _ = _root(tmp_path)
    text = autostart_entry.entry_text(settings, python, root)
    lines = text.splitlines()
    exec_line = next(ln for ln in lines if ln.startswith("Exec="))
    assert exec_line.endswith("-m sasha window --attach")
    assert f'env SASHA_ROOT="{root}"' in exec_line
    assert f"Name={settings.assistant.name}" in lines
    for line in ("Type=Application", "NoDisplay=true", "X-GNOME-Autostart-enabled=true", MARKER):
        assert line in lines
    assert entry_path(Path("/h")) == Path("/h/.config/autostart/assistant-window.desktop")


def test_on_with_yes_enables_the_services_and_writes_the_entry(
    settings: Settings, tmp_path: Path
) -> None:
    rc, ran, out = _on(settings, tmp_path)
    assert rc == 0
    assert ran[-1] == ["systemctl", "--user", "enable", BRAIN, LISTEN]
    path = entry_path(tmp_path / "home")
    assert path.is_file() and MARKER in path.read_text().splitlines()
    assert str(path) in out and "window --attach" in out
    assert "Undo: sasha autostart off" in out


def test_show_and_on_without_yes_write_nothing_outside(settings: Settings, tmp_path: Path) -> None:
    rc, ran, out = _on(settings, tmp_path, yes=False)
    assert rc == 1 and ran == [] and "[Desktop Entry]" in out
    assert not (tmp_path / "home").exists()
    root, python, home = _root(tmp_path / "b")
    lines: list[str] = []
    assert autostart("show", root, python=python, home=home, out=lines.append,
                     settings=settings) == 0  # fmt: skip
    assert not home.exists() and any("window --attach" in ln for ln in lines)


def test_off_removes_only_its_own_entry(settings: Settings, tmp_path: Path) -> None:
    _on(settings, tmp_path)
    root, _, home = tmp_path / "proj", None, tmp_path / "home"
    ran: list[Any] = []
    rc = autostart("off", root, home=home, run=lambda c: ran.append(list(c)) or 0,
                   out=lambda s: None, settings=settings)  # fmt: skip
    assert rc == 0 and ran == [UNDO.split()] and not entry_path(home).exists()
    # the owner's own file at that path stays, and says so
    path = entry_path(home)
    path.write_text("[Desktop Entry]\nExec=something-of-mine\n")
    rc = autostart("off", root, home=home, run=lambda c: 0, out=lambda s: None, settings=settings)
    assert rc == 1 and path.read_text().endswith("something-of-mine\n")
    path.unlink()
    target = tmp_path / "elsewhere.desktop"
    target.write_text(MARKER + "\n")
    path.symlink_to(target)
    rc = autostart("off", root, home=home, run=lambda c: 0, out=lambda s: None, settings=settings)
    assert rc == 1 and path.is_symlink() and target.exists()


def test_on_never_overwrites_an_entry_it_didnt_make(settings: Settings, tmp_path: Path) -> None:
    path = entry_path(tmp_path / "home")
    path.parent.mkdir(parents=True)
    path.write_text("[Desktop Entry]\nExec=mine\n")
    rc, _, out = _on(settings, tmp_path)
    assert rc == 1 and "no marker" in out and path.read_text() == "[Desktop Entry]\nExec=mine\n"


def test_the_cli_takes_attach() -> None:
    args = build_parser().parse_args(["window", "--attach"])
    assert args.attach is True
    assert build_parser().parse_args(["window"]).attach is False


# --- sasha window --attach ------------------------------------------------------------
@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    spawned: list[list[str]] = []
    monkeypatch.setattr(launch, "dead_proxy_port", lambda: 9)
    monkeypatch.setattr(launch, "is_running", lambda url: pytest.fail("attach never probes"))
    monkeypatch.setattr(launch, "browser_command", lambda url, d, p: ["chrome", url])
    yield spawned


def _spawn(spawned: list[list[str]]) -> Any:
    def spawn(cmd: list[str], **kwargs: Any) -> Any:
        assert "serve" not in cmd  # never a brain of its own
        spawned.append(cmd)
        return None

    return spawn


def test_attach_with_the_shell_opens_at_once_and_never_starts_a_brain(
    settings: Settings, no_server: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(launcher, "available", lambda: True)
    monkeypatch.setattr(launcher, "open_shell", lambda *a, **kw: calls.append(kw) or "started")
    rc = launch.open_window(settings, spawn=_spawn(no_server), attach=True)
    assert rc == 0 and calls[0]["attach"] is True and no_server == []


def test_attach_with_chrome_waits_then_opens_or_says_so(
    settings: Settings, no_server: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "available", lambda: False)
    rc = launch.open_window(settings, spawn=_spawn(no_server), attach=True, wait_up=lambda p: True)
    assert rc == 0 and no_server[0][0] == "chrome"
    no_server.clear()
    told: list[str] = []
    monkeypatch.setattr("sasha.app.single.notify", lambda text, **kw: told.append(text))
    rc = launch.open_window(settings, spawn=_spawn(no_server), attach=True, wait_up=lambda p: False)
    assert rc == 1 and no_server == []
    assert told and settings.assistant.name in told[0]


def test_the_shell_command_carries_the_wait_and_the_words(settings: Settings) -> None:
    cmd = launcher.shell_command(settings, 8765, 9, attach=True)
    i = cmd.index("--wait-for-brain")
    assert float(cmd[i + 1]) == waiting.ATTACH_WAIT_S
    name = settings.assistant.name
    assert cmd[cmd.index("--starting-notice") + 1].startswith(name)
    assert name in cmd[cmd.index("--late-notice") + 1] and "{name}" not in " ".join(cmd)
    assert "--wait-for-brain" not in launcher.shell_command(settings, 8765, 9)


# --- waiting for the brain ------------------------------------------------------------
def test_brain_wait_states() -> None:
    now = [0.0]
    answers = [False, False, True]
    wait = BrainWait(lambda: answers.pop(0), wait_s=1.0, clock=lambda: now[0],
                     sleep=lambda s: now.__setitem__(0, now[0] + s))  # fmt: skip
    assert wait.state() == "waiting"
    wait.run()
    assert wait.state() == "up" and now[0] == pytest.approx(1.0)
    late = BrainWait(lambda: False, wait_s=1.0, clock=lambda: now[0])
    now[0] += 2
    assert late.state() == "late"


def test_brain_up_asks_the_real_health_check_and_ignores_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b'{"ok": true}' if self.path == "/api/health" else b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    try:
        assert brain_up(server.server_address[1])
    finally:
        server.shutdown()
    assert not brain_up(server.server_address[1])  # nothing listens now


@pytest.mark.skipif(not HAS_QT, reason="the `app` extra (PySide6) is not installed")
def test_the_real_shell_shows_starting_then_late_then_the_page(tmp_path: Path) -> None:
    code = """
import json, pathlib, sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from sasha.app.shell import build, load_when_up
from sasha.app.waiting import BrainWait
app = QApplication([])
shell = build(app, 9, pathlib.Path(sys.argv[1]), 't', None, waiting='STARTING')
flag = [False]
wait = BrainWait(lambda: flag[0], wait_s=0.3)
out = {'first': (shell.label.text(), shell.stack.currentIndex())}
load_when_up(shell, wait, 'LATE')
def late():
    out['late'] = (shell.label.text(), shell.stack.currentIndex())
    flag[0] = True
def up():
    out['up'] = shell.stack.currentIndex()
    print('RESULT ' + json.dumps(out), flush=True)
    app.quit()
QTimer.singleShot(1200, late)
QTimer.singleShot(7000, up)
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
    import json

    r = json.loads(line[len("RESULT ") :])
    assert r["first"] == ["STARTING", 0]
    assert r["late"] == ["LATE", 0]
    assert r["up"] == 1  # the page is in front once the brain answers


def test_brain_up_never_follows_a_redirect() -> None:
    """Review A1: exactly one request to one address; a 3xx is "not up"."""
    hits = {"other": 0}

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits["other"] += 1
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    other = http.server.HTTPServer(("127.0.0.1", 0), Other)
    target = f"http://127.0.0.1:{other.server_address[1]}/elsewhere"

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    brain = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    for server in (other, brain):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert not brain_up(brain.server_address[1])
        assert hits["other"] == 0  # the other server never got the request
    finally:
        brain.shutdown()
        other.shutdown()
