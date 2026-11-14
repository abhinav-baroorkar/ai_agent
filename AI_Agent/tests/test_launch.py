from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sasha.cli import build_parser, main
from sasha.config import Settings
from sasha.server import launch
from sasha.server.security import allowed_origins, is_loopback
from sasha.server.voice import pcm_frames


def test_parser_has_serve_and_window() -> None:
    args = build_parser().parse_args(["serve", "--port", "9000"])
    assert args.command == "serve" and args.port == 9000 and args.host is None
    assert build_parser().parse_args(["window"]).command == "window"


def test_serve_refuses_non_loopback_host(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["serve", "--host", "0.0.0.0"]) == 2
    assert "not a loopback" in capsys.readouterr().err


def test_loopback_and_origins() -> None:
    assert all(is_loopback(h) for h in ("127.0.0.1", "localhost", "::1", "[::1]"))
    assert not is_loopback("lan.example") and not is_loopback("0.0.0.0")
    assert "http://localhost:8765" in allowed_origins(8765)
    assert "http://dev.local:5173" in allowed_origins(8765, ["http://dev.local:5173/"])


def test_browser_command_uses_isolated_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        launch.shutil, "which", lambda n: f"/usr/bin/{n}" if n == "chromium" else None
    )
    cmd = launch.browser_command("http://127.0.0.1:8765", tmp_path / "chrome-profile")
    assert cmd is not None and cmd[0] == "/usr/bin/chromium"
    assert "--app=http://127.0.0.1:8765" in cmd
    assert f"--user-data-dir={tmp_path / 'chrome-profile'}" in cmd
    for flag in ("--password-store=basic", "--disable-background-networking", "--no-pings"):
        assert flag in cmd
    monkeypatch.setattr(launch.shutil, "which", lambda n: None)
    assert launch.browser_command("http://x", tmp_path) is None


class FakeProc:
    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        self.terminated = True


def _settings_in(settings: Settings, tmp_path: Path) -> Settings:
    paths = settings.paths.model_copy(update={"root": tmp_path, "data_dir": tmp_path / "Data"})
    return settings.model_copy(update={"paths": paths})


def test_window_reuses_running_server(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[list[str]] = []

    def spawn(cmd: list[str], **_: Any) -> FakeProc:
        spawned.append(cmd)
        return FakeProc()

    monkeypatch.setattr(launch, "is_running", lambda url, timeout_s=1.5: True)
    monkeypatch.setattr(launch.shutil, "which", lambda n: "/usr/bin/google-chrome")
    s = _settings_in(settings, tmp_path)
    assert launch.open_window(s, spawn=spawn) == 0  # type: ignore[arg-type]
    assert len(spawned) == 1 and spawned[0][0] == "/usr/bin/google-chrome"
    assert (tmp_path / "Data" / "chrome-profile" / "tmp").is_dir()


def test_window_starts_server_then_browser(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[list[str]] = []
    up = iter([False, True])

    def spawn(cmd: list[str], **_: Any) -> FakeProc:
        spawned.append(cmd)
        return FakeProc()

    monkeypatch.setattr(launch, "is_running", lambda url, timeout_s=1.5: next(up, True))
    monkeypatch.setattr(launch.shutil, "which", lambda n: "/usr/bin/google-chrome")
    s = _settings_in(settings, tmp_path)
    assert launch.open_window(s, spawn=spawn) == 0  # type: ignore[arg-type]
    assert spawned[0][-5:] == ["serve", "--host", "127.0.0.1", "--port", "8765"]
    assert spawned[1][0] == "/usr/bin/google-chrome"


def test_window_without_chrome_does_not_open_default_browser(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spawned: list[list[str]] = []
    monkeypatch.setattr(launch, "is_running", lambda url, timeout_s=1.5: True)
    monkeypatch.setattr(launch.shutil, "which", lambda n: None)
    s = _settings_in(settings, tmp_path)
    rc = launch.open_window(s, spawn=lambda cmd, **_: spawned.append(cmd))  # type: ignore[arg-type,func-returns-value]
    assert rc == 0 and spawned == []
    assert "Open http://127.0.0.1:8765 yourself" in capsys.readouterr().out


def test_window_reports_server_that_never_starts(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = FakeProc()
    monkeypatch.setattr(launch, "is_running", lambda url, timeout_s=1.5: False)
    monkeypatch.setattr(launch, "wait_until_up", lambda url, p, t: False)
    rc = launch.open_window(_settings_in(settings, tmp_path), spawn=lambda c, **_: proc)  # type: ignore[arg-type,return-value]
    assert rc == 1 and proc.terminated


def test_browser_env_keeps_files_in_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    env = launch.browser_env(tmp_path / "chrome-profile")
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMPDIR"):
        assert Path(env[key]).is_relative_to(tmp_path / "chrome-profile")
    assert env["DISPLAY"] == ":0"


def test_pcm_frames_are_20ms_whole_samples() -> None:
    frames = list(pcm_frames(b"\x00" * 1000, 16000))
    assert [len(f) for f in frames] == [640, 360]


@pytest.mark.parametrize("host", ["example.com", "192.0.2.1", "0.0.0.0"])
def test_window_refuses_non_loopback_host(
    settings: Settings, tmp_path: Path, host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed: list[str] = []
    monkeypatch.setattr(launch, "is_running", lambda url, timeout_s=1.5: bool(probed.append(url)))
    spawned: list[Any] = []
    s = _settings_in(settings, tmp_path)
    assert launch.open_window(s, host=host, spawn=lambda c, **_: spawned.append(c)) == 2  # type: ignore[arg-type,func-returns-value]
    assert probed == [] and spawned == []


def test_window_refuses_remote_host_from_profile(settings: Settings, tmp_path: Path) -> None:
    s = _settings_in(settings, tmp_path)
    server = s.profile.server.model_copy(update={"host": "example.com"})
    s = s.model_copy(update={"profile": s.profile.model_copy(update={"server": server})})
    assert launch.open_window(s, spawn=lambda c, **_: None) == 2  # type: ignore[arg-type,return-value]
