"""The window on a wake (2026-11-16): the start check waited for nothing, so every
wake-open was called a failure and the Chrome window opened; and the shell ended with
nothing in its log. Fake spawns and pipes only: no window, no unit."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any

import pytest

from sasha.app import exit_note, launcher, start_check
from sasha.app.exit_note import ExitNotes
from sasha.config import Settings
from tests.test_shell_start_check import local  # noqa: F401  (the fixture)


class Proc:
    def __init__(self, rc: int, events: list[str]) -> None:
        self.rc, self.events = rc, events

    def wait(self, timeout: float) -> int:
        self.events.append("systemd-run returned")
        return self.rc


def _unit_open(
    settings: Settings, rc: int, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, list[str]]:
    monkeypatch.setattr(launcher.shutil, "which", lambda n: "/usr/bin/systemd-run")
    events: list[str] = []

    def spawn(cmd: list[str], **kw: Any) -> Proc:
        events.append("spawned")
        return Proc(rc, events)

    def alive() -> bool:
        events.append("alive?")
        return True

    pings = iter([False, True])
    how = launcher.open_shell(
        settings,
        8765,
        9,
        wake=True,
        as_unit=True,
        spawn=spawn,
        ask=lambda p, c: c == "ping" and next(pings),
        unit_alive=alive,
    )
    return how, events


def test_the_unit_is_checked_only_after_systemd_run_returned(
    local: Settings,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    how, events = _unit_open(local, 0, monkeypatch)
    assert how == "started"
    assert events[:3] == ["spawned", "systemd-run returned", "alive?"]


def test_systemd_run_failing_is_a_failure_with_a_reason(
    local: Settings,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    how, events = _unit_open(local, 1, monkeypatch)
    assert how == "failed" and "alive?" not in events
    text = start_check.first_lines(start_check.log_path(local))
    assert "exit 1" in text
    assert start_check.failure_reason(text) == "systemd couldn't start the window unit"


def test_a_slow_systemd_run_leaves_it_to_the_start_check() -> None:
    import subprocess

    class Slow:
        def wait(self, timeout: float) -> int:
            raise subprocess.TimeoutExpired("systemd-run", timeout)

    assert launcher._wait(Slow()) == 0 and launcher._wait(None) == 0


def _notes(tmp_path: Path) -> tuple[ExitNotes, Path]:
    path = tmp_path / "start.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    now = iter([10.0, 14.25, 14.5, 15.0])
    notes = ExitNotes(fd, clock=lambda: next(now))
    os.close(fd)  # the notes keep their own copy, as after quiet_stderr
    return notes, path


def test_every_way_out_leaves_a_line(tmp_path: Path) -> None:
    notes, path = _notes(tmp_path)
    notes.write(exit_note.CLOSED)
    notes.ended(0)
    lines = path.read_text().splitlines()
    assert lines == [
        "[shell] the window was closed (4.2 s after start)",
        "[shell] ended (exit code 0) (4.5 s after start)",
    ]
    assert start_check.failure_reason("\n".join(lines)) == "the window was closed as it opened"


class FakeSignal:
    def __init__(self) -> None:
        self.slots: list[Any] = []

    def connect(self, slot: Any) -> None:
        self.slots.append(slot)


class FakeTimer:
    def __init__(self) -> None:
        self.timeout = FakeSignal()
        self.ms = 0

    def start(self, ms: int) -> None:
        self.ms = ms


class FakeApp:
    def __init__(self) -> None:
        self.lastWindowClosed = FakeSignal()
        self.quits = 0

    def quit(self) -> None:
        self.quits += 1
        for slot in self.lastWindowClosed.slots:
            slot()  # quitting closes the window, as Qt does


def test_a_signal_is_named_and_quits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(exit_note.signal, "signal", lambda s, h: handlers.__setitem__(s, h))
    notes, path = _notes(tmp_path)
    app = FakeApp()
    notes.watch(app, timer=FakeTimer)  # no Qt in the test process
    assert set(handlers) == {signal.SIGTERM, signal.SIGHUP, signal.SIGINT}
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    text = path.read_text()
    assert app.quits == 1 and "stopped by SIGTERM" in text
    assert exit_note.CLOSED not in text  # the quit's own close is not a second reason
    assert start_check.failure_reason(text) == "something stopped it with a signal"


def test_a_crash_is_named() -> None:
    text = "Fatal Python error: Segmentation fault\n\nCurrent thread 0x1 (most recent call first):"
    assert "crashed" in start_check.failure_reason(text)


def test_a_launcher_note_appends_and_stays_private(local: Settings) -> None:  # noqa: F811
    os.close(start_check.open_log(local))
    start_check.note(local, "first")
    start_check.note(local, "second")
    path = start_check.log_path(local)
    assert path.read_text() == "[launcher] first\n[launcher] second\n"
    assert path.stat().st_mode & 0o777 == 0o600
