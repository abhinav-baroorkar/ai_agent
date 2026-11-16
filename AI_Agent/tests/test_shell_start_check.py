"""The app shell must really come up, or the Chrome window opens and says why (found
on the owner's display: Qt 6.5+ needs libxcb-cursor0). Fake spawns only: no window."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from sasha import cli
from sasha.app import launcher, start_check
from sasha.config import Settings
from sasha.listener import app as listener_app
from sasha.server import launch

QT_ERROR = (
    "qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load the Qt "
    "xcb platform plugin.\nThis application failed to start because no Qt platform plugin "
    "could be initialized. #t=0123456789abcdef0123456789abcdef\n"
)


class DeadProc:
    def poll(self) -> int:
        return -6


@pytest.fixture
def local(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    os.chmod(run, 0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("DISPLAY", ":0")
    paths = settings.paths.model_copy(update={"data_dir": tmp_path / "Data"})
    return settings.model_copy(update={"paths": paths})


def _dying_shell(spawned: list[list[str]]) -> Any:
    def spawn(cmd: list[str], **kw: Any) -> Any:
        spawned.append(cmd)
        if "sasha.app.shell" in cmd:
            os.write(kw["stderr"], QT_ERROR.encode())  # what Qt prints, then it aborts
            return DeadProc()
        return None  # the Chrome window

    return spawn


def test_a_shell_that_dies_at_once_is_failed_not_started(local: Settings) -> None:
    spawned: list[list[str]] = []
    how = launcher.open_shell(local, 8765, 9, spawn=_dying_shell(spawned), ask=lambda p, c: False)
    assert how == "failed"
    log = start_check.log_path(local)
    assert log.stat().st_mode & 0o777 == 0o600
    lines = start_check.first_lines(log)
    assert "xcb-cursor0" in lines and "0123456789abcdef" not in lines  # token-like: masked
    assert start_check.failure_reason(lines) == start_check.MISSING_CURSOR


@pytest.fixture
def window(local: Settings, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    told: list[str] = []
    monkeypatch.setattr(launcher, "available", lambda: True)
    monkeypatch.setattr(launch, "dead_proxy_port", lambda: 9)
    monkeypatch.setattr(launch, "is_running", lambda url: True)
    monkeypatch.setattr(launch, "browser_command", lambda url, d, p: ["chrome", url])
    monkeypatch.setattr(launch, "read_token", lambda d: None)
    monkeypatch.setattr("sasha.app.single.notify", lambda text, **kw: told.append(text))
    monkeypatch.setattr("sasha.app.single.ask_window", lambda p, c, timeout=1.0: False)
    monkeypatch.setattr(launcher, "ask_window", lambda p, c, timeout=1.0: False)
    return told


def test_sasha_window_falls_back_to_chrome_and_says_why(
    local: Settings, window: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    spawned: list[list[str]] = []
    assert launch.open_window(local, spawn=_dying_shell(spawned)) == 0
    assert spawned[-1][0] == "chrome"  # the browser window instead
    expected = (
        "The app window couldn't start (a system package is missing: libxcb-cursor0). "
        "Opened the browser window instead."
    )
    err = capsys.readouterr()
    assert expected in err.err and "sudo apt install libxcb-cursor0" in err.err
    assert "Opened http://127.0.0.1:8765." not in err.out  # never for the shell that died
    assert "Opened http://127.0.0.1:8765 in an app window." in err.out  # the Chrome one
    assert window == [expected]


def test_attach_falls_back_too(local: Settings, window: list[str]) -> None:
    spawned: list[list[str]] = []
    rc = launch.open_window(local, spawn=_dying_shell(spawned), attach=True, wait_up=lambda p: True)
    assert rc == 0 and spawned[-1][0] == "chrome" and "libxcb-cursor0" in window[0]


def test_the_wake_path_opens_the_browser_window_and_tells_the_owner(
    local: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[str] = []
    opened: list[str] = []
    monkeypatch.setattr(launcher, "available", lambda: True)
    monkeypatch.setattr(launcher, "open_shell", lambda *a, **kw: "failed")
    monkeypatch.setattr("sasha.server.launch.dead_proxy_port", lambda: 9)
    monkeypatch.setattr(
        "sasha.server.launch._open_chrome", lambda url, s, p, sp: opened.append(url)
    )
    monkeypatch.setattr("sasha.app.single.notify", lambda text, **kw: told.append(text))
    start_check.log_path(local).parent.mkdir(parents=True, exist_ok=True)
    start_check.log_path(local).write_text(QT_ERROR)
    opener = listener_app.window_opener(local, lambda: True)
    assert opener.on_wake(command=True) == "browser"
    assert opened and "libxcb-cursor0" in told[0]
    assert len(opener.opens) == 1  # counted like an open, for the limits


def test_confirm() -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(s: float) -> None:
        now[0] += s

    assert start_check.confirm(lambda: True, lambda: True, clock=clock, sleep=sleep)
    assert not start_check.confirm(lambda: False, lambda: False, clock=clock, sleep=sleep)
    assert start_check.confirm(lambda: False, lambda: True, timeout=1, clock=clock, sleep=sleep)


def test_failure_reasons() -> None:
    assert start_check.failure_reason("could not connect to display :1") == (
        "it couldn't reach the screen"
    )
    assert start_check.failure_reason("") == "it stopped as soon as it started"


def test_doctor_says_whether_the_app_window_can_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "available", lambda: False)
    assert cli.app_window_line().startswith("[info] App window: not installed")
    monkeypatch.setattr(launcher, "available", lambda: True)
    monkeypatch.setattr(start_check, "cursor_library_present", lambda: False)
    line = cli.app_window_line()
    assert "libxcb-cursor0" in line and "sudo apt install libxcb-cursor0" in line
    monkeypatch.setattr(start_check, "cursor_library_present", lambda: True)
    assert cli.app_window_line() == "[ok]   App window: ready"
