"""The window on a wake: caps, the lock, false wakes; the launcher; the socket protocol."""

from __future__ import annotations

import os
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
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    os.chmod(run, 0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))  # a private runtime folder of ours
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
    assert "--setenv=DISPLAY=:0" in unit  # the session's display reaches the unit
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert launcher.open_shell(settings, 8765, 9, spawn=spawn, ask=lambda p, c: False) == (
        "no_session"  # never a socket in a shared folder such as /tmp
    )
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
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


def test_the_mic_closes_before_a_slow_window_step(settings: Settings) -> None:
    """Review A1: busy is set and the mic closed at once; the window comes up later."""
    import asyncio
    import time as _time

    from sasha.listener import messages

    order: list[str] = []

    class SlowOpener:
        config = WindowConfig()

        def on_wake(self, command: bool) -> str:
            _time.sleep(0.5)  # a socket timeout plus a spawn
            order.append("window")
            return "started"

        def check_closed(self) -> None:
            pass

    class Indicator:
        def earcon(self, kind: str) -> Any:
            raise RuntimeError("no sound in this test")

    class Lst:
        busy = False
        window = SlowOpener()
        indicator = Indicator()

        def _changed(self) -> None:
            order.append(f"changed busy={self.busy}")

    async def main() -> float:
        lst = Lst()
        t0 = _time.perf_counter()
        await messages._on_wake(lst, {"type": "wake", "heard": True, "command": True})  # type: ignore[arg-type]
        took = _time.perf_counter() - t0
        assert lst.busy and order == ["changed busy=True"]  # mic closed, window not yet
        await asyncio.sleep(0.8)
        return took

    took = asyncio.run(main())
    assert took < 0.2 and order[-1] == "window"


def _read_exec(line: str) -> list[str]:
    """Undo the Desktop Entry spec: the string layer, then Exec's quoting."""
    value = line.removeprefix("Exec=").replace("\\\\", "\x00").replace("\x00", "\\")
    args, cur, quoted, i = [], "", False, 0
    while i < len(value):
        c = value[i]
        if quoted and c == "\\" and i + 1 < len(value):
            cur, i = cur + value[i + 1], i + 2
            continue
        if c == '"':
            quoted = not quoted
        elif c == " " and not quoted:
            args, cur = [*args, cur], ""
        else:
            cur += c
        i += 1
    return [*args, cur]


def test_the_menu_entry_escapes_odd_paths_and_off_removes_only_its_own(
    settings: Settings, tmp_path: Path
) -> None:
    from sasha.app.menu import MARKER, app_menu, entry_path

    home, root = tmp_path / "home", tmp_path / 'pro$ject `x` \\ "q"'
    py = Path("/opt/a\\b/py%env/bin/python")
    app_menu("on", settings, root, yes=True, home=home, python=py, out=lambda _: None)
    lines = entry_path(home).read_text().splitlines()
    exec_line = next(x for x in lines if x.startswith("Exec="))
    args = _read_exec(exec_line)
    assert args[:2] == ["env", f"SASHA_ROOT={root}"]
    assert args[2].replace("%%", "%") == str(py) and args[3:] == ["-m", "sasha", "window"]
    assert MARKER in lines
    other = entry_path(home)
    other.write_text("[Desktop Entry]\nName=Something else\n")  # not ours
    out: list[str] = []
    assert app_menu("off", settings, root, home=home, out=out.append) == 1
    assert other.exists() and "no marker" in out[-1]
    other.unlink()
    other.symlink_to(tmp_path / "elsewhere")
    assert app_menu("off", settings, root, home=home, out=out.append) == 1


def test_the_socket_lives_only_in_a_private_runtime_folder_of_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "run"
    private.mkdir(mode=0o700)
    os.chmod(private, 0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(private))
    assert single.socket_path() == private / single.SOCKET_NAME
    os.chmod(private, 0o755)  # others may enter: refused
    with pytest.raises(single.NoRuntimeDir):
        single.socket_path()
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/")  # not ours (root's)
    with pytest.raises(single.NoRuntimeDir):
        single.socket_path()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    with pytest.raises(single.NoRuntimeDir):
        single.socket_path()
