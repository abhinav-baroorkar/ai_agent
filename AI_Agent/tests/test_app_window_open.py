"""The window on a wake: caps, the lock, false wakes; the launcher; the socket protocol."""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from sasha.app import launcher, single
from sasha.config import Settings
from sasha.config_app import WindowConfig
from sasha.listener.window_open import WindowOpener


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def opener(**kw: Any) -> tuple[WindowOpener, list[str], Clock, dict[str, bool]]:
    calls: list[str] = []
    state = {"allowed": True, "open": True}
    clock = Clock()

    def open_window() -> str:
        calls.append("open")
        return "started"

    o = WindowOpener(
        WindowConfig(**kw), lambda: state["allowed"], open_window, lambda: state["open"], clock
    )
    return o, calls, clock, state


def test_never_while_locked_and_at_most_one_per_interval() -> None:
    o, calls, clock, state = opener()
    state["allowed"] = False
    assert o.on_wake(command=True) == "locked" and calls == []
    state["allowed"] = True
    assert o.on_wake(command=True) == "started"
    clock.now += 29
    assert o.on_wake(command=True) == "limit"
    clock.now += 2
    assert o.on_wake(command=True) == "started" and len(calls) == 2


def test_six_an_hour() -> None:
    o, calls, clock, _ = opener()
    for _ in range(6):
        assert o.on_wake(command=True) == "started"
        clock.now += 31
    assert o.on_wake(command=True) == "limit"
    clock.now += 3600
    assert o.on_wake(command=True) == "started" and len(calls) == 7


def test_three_false_wakes_in_ten_minutes_pause_raising() -> None:
    o, _, clock, state = opener()
    for _ in range(3):  # the phrase alone, then the armed window runs out
        assert o.on_wake(command=False) == "started"
        o.on_armed_expired()
        clock.now += 60
    assert o.on_wake(command=True) == "paused"
    clock.now += 1800
    assert o.on_wake(command=True) == "started"
    # the phrase alone, then the command: not a false wake, and no second raise
    clock.now += 60
    assert o.on_wake(command=False) == "started"
    assert o.on_wake(command=True) == "already_up"
    o.on_armed_expired()
    assert len(o.false_wakes) == 0
    # a window closed within 10 s of a wake-open counts as a false wake
    clock.now += 60
    o.on_wake(command=True)
    state["open"] = False
    clock.now += 10
    o.check_closed()
    assert len(o.false_wakes) == 1


def test_off_in_config() -> None:
    o, calls, _, _ = opener(open_on_wake=False)
    assert o.on_wake(command=True) == "off" and calls == []


def test_the_launcher_raises_an_open_window_or_starts_one(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[tuple[list[str], dict[str, Any]]] = []

    def spawn(cmd: list[str], **kw: Any) -> None:
        spawned.append((cmd, kw))

    monkeypatch.setenv("DISPLAY", ":0")
    raised = launcher.open_shell(settings, 8765, 9, wake=True, spawn=spawn, ask=lambda p, c: True)
    assert raised == "raised" and spawned == []
    got = launcher.open_shell(settings, 8765, 9, spawn=spawn, ask=lambda p, c: False)
    assert got == "started" and spawned[0][1]["start_new_session"] is True
    cmd = spawned[0][0]
    assert cmd[1:3] == ["-m", "sasha.app.shell"] and "--wake" not in cmd
    assert cmd[cmd.index("--title") + 1] == settings.assistant.name
    assert settings.assistant.name in cmd[cmd.index("--notice") + 1]  # from assistant.yaml
    monkeypatch.setattr(launcher.shutil, "which", lambda n: "/usr/bin/systemd-run")
    launcher.open_shell(
        settings, 8765, 9, wake=True, as_unit=True, spawn=spawn, ask=lambda p, c: False
    )
    unit = spawned[1][0]
    assert unit[:3] == ["systemd-run", "--user", f"--unit={single.DESKTOP_ID}"]
    assert unit[-1] == "--wake"
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert launcher.open_shell(settings, 8765, 9, spawn=spawn, ask=lambda p, c: False) == (
        "no_display"
    )


def test_the_socket_protocol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert single.parse(b"raise\n") == ("raise", None)
    assert single.parse(b"wake abc:123_x\n") == ("wake", "abc:123_x")
    assert single.parse(b"wake bad token\n") is None
    assert single.parse(b"open /etc\n") is None
    monkeypatch.chdir(tmp_path)  # a relative path: pytest's folder is too long for AF_UNIX
    path = Path("s.sock")
    got: list[bytes] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def answer() -> None:
        conn, _ = server.accept()
        got.append(conn.recv(256))
        conn.sendall(b"ok\n")
        conn.close()

    t = threading.Thread(target=answer)
    t.start()
    monkeypatch.setenv("XDG_ACTIVATION_TOKEN", "tok_1")
    assert single.ask_window(path, "wake") is True
    t.join(2)
    server.close()
    assert got == [b"wake tok_1\n"]
    assert single.ask_window(Path("missing.sock"), "raise") is False
    with pytest.raises(ValueError):
        single.ask_window(path, "quit")


def test_the_notification_names_the_menu_entry() -> None:
    ran: list[list[str]] = []
    single.notify("Listening", run=lambda cmd, **kw: ran.append(cmd))
    assert "org.freedesktop.Notifications.Notify" in ran[0]
    assert any(single.DESKTOP_ID in a and "desktop-entry" in a for a in ran[0])


def test_the_menu_entry_with_a_fake_home(settings: Settings, tmp_path: Path) -> None:
    from sasha.app.menu import app_menu, entry_path

    home, lines = tmp_path / "home", []
    root = tmp_path / "project"
    py = Path("/opt/py env/bin/python")  # a space and no oddities: quoted in Exec
    assert app_menu("show", settings, root, home=home, python=py, out=lines.append) == 0
    assert not entry_path(home).exists()
    app_menu("on", settings, root, home=home, python=py, out=lines.append)  # no --yes
    assert not entry_path(home).exists() and any("--yes" in x for x in lines)
    app_menu("on", settings, root, yes=True, home=home, python=py, out=lines.append)
    text = entry_path(home).read_text()
    assert f"Name={settings.assistant.name}\n" in text
    assert f"StartupWMClass={single.DESKTOP_ID}\n" in text
    assert 'Exec=env SASHA_ROOT="' in text and '"/opt/py env/bin/python" -m sasha window' in text
    assert entry_path(home).parent == home / ".local" / "share" / "applications"
    assert [p.name for p in home.rglob("*") if p.is_file()] == [f"{single.DESKTOP_ID}.desktop"]
    app_menu("off", settings, root, home=home, out=lines.append)
    assert not entry_path(home).exists()
