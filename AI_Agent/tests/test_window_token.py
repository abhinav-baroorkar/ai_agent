"""The per-launch window token (take-over design 9.1, review C4): file, routes, launcher."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sasha.config import Settings, load_settings
from sasha.server import launch
from sasha.server.app import create_app
from sasha.server.window_token import (
    START_PAGE,
    TOKEN_FILE,
    WindowToken,
    read_token,
    remove_token,
    write_start_page,
    write_token,
)
from tests.server_fakes import BASE_URL, WS, Harness

TOKEN = "cd" * 32
LOCAL = ("127.0.0.1", 50000)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_the_file_is_private_from_the_start_and_atomic(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    os.chmod(state, 0o755)
    outside = tmp_path / "outside"
    outside.write_text("keep")
    planted = state / f".{TOKEN_FILE}.{os.getpid()}.tmp"
    planted.symlink_to(outside)  # a link planted at the temporary name is removed
    path = write_token(state, WindowToken(TOKEN))
    assert path.read_text() == TOKEN and _mode(path) == 0o600 and _mode(state) == 0o700
    assert outside.read_text() == "keep" and not planted.exists()
    assert read_token(state) == TOKEN


def test_only_a_private_well_formed_file_of_ours_is_read(tmp_path: Path) -> None:
    write_token(tmp_path, WindowToken(TOKEN))
    os.chmod(tmp_path / TOKEN_FILE, 0o644)
    assert read_token(tmp_path) is None  # readable by others: ignored
    (tmp_path / TOKEN_FILE).unlink()
    target = tmp_path / "real"
    target.write_text(TOKEN)
    os.chmod(target, 0o600)
    (tmp_path / TOKEN_FILE).symlink_to(target)
    assert read_token(tmp_path) is None  # a link: ignored
    (tmp_path / TOKEN_FILE).unlink()
    write_token(tmp_path, WindowToken("not hex" * 10))
    assert read_token(tmp_path) is None


def test_removal_only_takes_our_own_token(tmp_path: Path) -> None:
    ours, newer = WindowToken(TOKEN), WindowToken()
    write_token(tmp_path, newer)  # another brain started since
    remove_token(tmp_path, ours)
    assert read_token(tmp_path) == newer.value
    remove_token(tmp_path, newer)
    assert not (tmp_path / TOKEN_FILE).exists()


def test_matching_is_strict() -> None:
    token = WindowToken(TOKEN)
    assert token.matches(TOKEN)
    assert not token.matches(None) and not token.matches("") and not token.matches(TOKEN[:-1])
    assert not token.matches(TOKEN.upper()) and not token.matches("ef" * 32)
    assert token.in_subprotocols(["brain.v1", f"token.{TOKEN}"])
    assert not token.in_subprotocols([f"token.{TOKEN}"])  # brain.v1 is required
    assert not token.in_subprotocols(["brain.v1", "token.x"])


def _client(tmp_path: Path, **kw: Any) -> tuple[TestClient, Harness]:
    harness = Harness(load_settings(), tmp_path)
    harness.deps.window_token = WindowToken(TOKEN)
    app = create_app(harness.deps, web_dir=None)
    return TestClient(app, base_url=BASE_URL, client=LOCAL, **kw), harness


def test_browser_routes_need_the_token(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    for headers in ({}, {"x-window-token": "ef" * 32}, {"x-window-token": "nonsense"}):
        res = client.get("/api/browser", headers=headers)
        assert res.status_code == 401 and TOKEN not in res.text
        assert client.post("/api/browser/stop", headers=headers).status_code == 401
    assert client.get("/api/browser", headers={"x-window-token": TOKEN}).status_code == 200
    assert client.get("/api/health").status_code == 200  # other routes: later (S3)


def test_the_socket_needs_the_token_as_a_subprotocol(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    url = f"{WS}/ws/browser"
    for protocols in (None, ["brain.v1"], ["brain.v1", "token." + "ef" * 32], [f"token.{TOKEN}"]):
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            client.websocket_connect(  # a fresh dict: the client writes the protocols into it
                url, headers={"origin": BASE_URL}, subprotocols=protocols
            ) as ws,
        ):
            ws.receive_json()
        assert refused.value.code == 1008 and TOKEN not in refused.value.reason
    with client.websocket_connect(
        url, headers={"origin": BASE_URL}, subprotocols=["brain.v1", f"token.{TOKEN}"]
    ) as ws:
        assert ws.accepted_subprotocol == "brain.v1"  # never the token
        assert ws.receive_json()["type"] == "browser_state"


def test_the_server_writes_the_token_at_start_and_removes_it_at_stop(tmp_path: Path) -> None:
    client, harness = _client(tmp_path)
    state = harness.settings.paths.state_dir
    with client:
        assert read_token(state) == TOKEN and _mode(state / TOKEN_FILE) == 0o600
    assert not (state / TOKEN_FILE).exists()


def test_the_start_page_carries_the_token_privately(tmp_path: Path) -> None:
    page = write_start_page(tmp_path / "chrome-profile", "http://127.0.0.1:8765", TOKEN)
    assert page.name == START_PAGE and _mode(page) == 0o600 and _mode(page.parent) == 0o700
    assert f'location.replace("http://127.0.0.1:8765/#t={TOKEN}")' in page.read_text()


class _Proc:
    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        pass


def _settings_in(settings: Settings, tmp_path: Path) -> Settings:
    paths = settings.paths.model_copy(update={"root": tmp_path, "data_dir": tmp_path / "Data"})
    return settings.model_copy(update={"paths": paths})


def test_the_window_opens_on_the_start_page_never_with_the_token_on_its_command_line(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[list[str]] = []
    monkeypatch.setattr(launch, "is_running", lambda url, timeout_s=1.5: True)
    monkeypatch.setattr(launch.shutil, "which", lambda n: "/usr/bin/google-chrome")
    monkeypatch.setattr(launch, "dead_proxy_port", lambda: 9)
    s = _settings_in(settings, tmp_path)
    write_token(s.paths.state_dir, WindowToken(TOKEN))
    assert launch.open_window(s, spawn=lambda cmd, **kw: spawned.append(cmd) or _Proc()) == 0  # type: ignore[arg-type,func-returns-value]
    argv = " ".join(spawned[0])
    page = s.paths.data_dir / "chrome-profile" / START_PAGE
    assert f"--app={page.as_uri()}" in spawned[0] and TOKEN not in argv
    assert TOKEN in page.read_text()


def test_without_a_token_file_the_window_opens_plainly_and_says_so(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spawned: list[list[str]] = []
    monkeypatch.setattr(launch, "is_running", lambda url, timeout_s=1.5: True)
    monkeypatch.setattr(launch.shutil, "which", lambda n: "/usr/bin/google-chrome")
    monkeypatch.setattr(launch, "dead_proxy_port", lambda: 9)
    s = _settings_in(settings, tmp_path)
    assert launch.open_window(s, spawn=lambda cmd, **kw: spawned.append(cmd) or _Proc()) == 0  # type: ignore[arg-type,func-returns-value]
    assert "--app=http://127.0.0.1:8765" in spawned[0]
    assert "browser panel will be refused" in capsys.readouterr().out
