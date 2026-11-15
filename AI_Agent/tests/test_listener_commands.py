"""`sasha autostart`, `sasha listen` flags, the debug dump, and refusals."""

from __future__ import annotations

import wave
from collections.abc import Sequence
from pathlib import Path

import pytest

from sasha.cli import build_parser
from sasha.listener.autostart import BRAIN, LISTEN, UNDO, autostart, unit_texts


def _root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    python = root / "Dependencies" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    return root, python


def test_unit_files_follow_the_design(tmp_path: Path) -> None:
    root, python = _root(tmp_path)
    units = unit_texts(root, python)
    for name in (BRAIN, LISTEN):
        text = units[name]
        assert f"WorkingDirectory={root}" in text
        for line in (
            "UMask=0077",
            "KillMode=control-group",
            "Restart=on-failure",
            "RestartSec=5",
            "StartLimitBurst=5",
            "StartLimitIntervalSec=600",
            "PartOf=graphical-session.target",
            "WantedBy=graphical-session.target",
        ):
            assert line in text, (name, line)
        for var in ("SASHA_ROOT", "HF_HOME", "XDG_CACHE_HOME", "TMPDIR", "UV_CACHE_DIR"):
            value = next(ln for ln in text.splitlines() if ln.startswith(f'Environment="{var}='))
            assert value.split("=", 2)[2].strip('"').startswith(str(root)), value
        assert "HOME=" not in text.replace("_HOME=", "")
    assert f'ExecStart="{python}" -m sasha serve' in units[BRAIN]
    assert f'ExecStart="{python}" -m sasha listen' in units[LISTEN]
    assert f"Wants={BRAIN}" in units[LISTEN]
    assert (
        f"After={BRAIN} graphical-session.target pipewire.service wireplumber.service"
        in (units[LISTEN])
    )


def test_on_without_yes_prints_but_never_links(tmp_path: Path) -> None:
    root, python = _root(tmp_path)
    ran: list[Sequence[str]] = []
    out: list[str] = []
    rc = autostart(
        "on",
        root,
        python=python,
        home=tmp_path / "home",
        run=lambda c: ran.append(c) or 0,
        out=out.append,
    )
    assert rc == 1 and ran == []
    text = "\n".join(out)
    assert "[Service]" in text and "UMask=0077" in text
    assert str(tmp_path / "home" / ".config" / "systemd" / "user" / BRAIN) in text
    assert UNDO in text and "--yes" in text
    assert (root / "Data" / "autostart" / BRAIN).is_file()


def test_on_with_yes_links_then_enables(tmp_path: Path) -> None:
    root, python = _root(tmp_path)
    ran: list[list[str]] = []
    rc = autostart(
        "on",
        root,
        yes=True,
        python=python,
        home=tmp_path / "home",
        run=lambda c: ran.append(list(c)) or 0,
        out=lambda s: None,
    )
    folder = root / "Data" / "autostart"
    assert rc == 0
    assert ran == [
        ["systemctl", "--user", "link", str(folder / BRAIN)],
        ["systemctl", "--user", "link", str(folder / LISTEN)],
        ["systemctl", "--user", "enable", BRAIN, LISTEN],
    ]


def test_off_and_show(tmp_path: Path) -> None:
    root, python = _root(tmp_path)
    ran: list[list[str]] = []
    assert autostart("off", root, run=lambda c: ran.append(list(c)) or 0, out=lambda s: None) == 0
    assert ran == [UNDO.split()]
    ran.clear()
    assert (
        autostart(
            "show", root, python=python, run=lambda c: ran.append(list(c)) or 0, out=lambda s: None
        )
        == 0
    )
    assert ran == []


def test_python_outside_the_project_is_refused(tmp_path: Path) -> None:
    root, _ = _root(tmp_path)
    out: list[str] = []
    assert autostart("show", root, python=Path("/usr/bin/python3"), out=out.append) == 2
    assert "[refused]" in out[0]


def test_listen_flags_only_from_the_cli() -> None:
    args = build_parser().parse_args(["listen"])
    assert args.debug_dump_audio is False and args.no_pause_on_battery is False
    args = build_parser().parse_args(["listen", "--debug-dump-audio", "--end-silence-ms", "800"])
    assert args.debug_dump_audio is True and args.end_silence_ms == 800


def test_profiles_cannot_turn_the_debug_dump_on() -> None:
    from sasha.config import Profile

    assert not any("debug" in name for name in Profile.model_fields)


def test_debug_dump_writes_owner_only_wav(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    from sasha.listener.debug_dump import DebugDump
    from sasha.listener.gate import Segment

    dump = DebugDump(tmp_path)
    dump(Segment(pcm=b"\x01\x00" * 1600, speech_ms=100))
    files = list((tmp_path / "debug-audio").rglob("*.wav"))
    assert len(files) == 1 and oct(files[0].stat().st_mode & 0o777) == "0o600"
    with wave.open(str(files[0])) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 1600)


def test_listen_refuses_without_the_indicator_module(capsys: pytest.CaptureFixture[str]) -> None:
    import importlib.util

    if importlib.util.find_spec("sasha.listener.indicator") is not None:
        pytest.skip("indicator present")
    from sasha.cli import main

    assert main(["listen"]) == 2
    assert "microphone was not opened" in capsys.readouterr().err


def test_vad_missing_is_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("faster_whisper")
    from sasha.listener import vad

    monkeypatch.setattr("faster_whisper.vad.get_assets_path", lambda: "/nonexistent")
    with pytest.raises(vad.VadUnavailableError, match=r"faster-whisper >= 1\.2"):
        vad.silero_model_path()


def test_worktree_python_is_refused(tmp_path: Path) -> None:
    root, _ = _root(tmp_path)
    wt = root / "Dependencies" / "worktrees" / "feat-x" / "AI_Agent" / ".venv" / "bin" / "python"
    out: list[str] = []
    assert autostart("show", root, python=wt, out=out.append) == 2
    assert "worktree" in out[0]


def test_paths_are_quoted_and_next_login_is_said(tmp_path: Path) -> None:
    root = tmp_path / "my proj"
    python = root / "Dependencies" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    text = unit_texts(root, python)[LISTEN]
    assert f'ExecStart="{python}" -m sasha listen' in text
    assert f'Environment="SASHA_ROOT={root}"' in text
    assert f"WorkingDirectory={root}" in text
    out: list[str] = []
    autostart("on", root, yes=True, python=python, home=tmp_path, run=lambda c: 0, out=out.append)
    assert any("next graphical login" in line for line in out)


def test_listener_refuses_a_brain_that_is_not_on_this_machine() -> None:
    """Review L4: gated room audio never leaves the machine."""
    from sasha.config import load_settings
    from sasha.listener.app import ListenerUnavailableError, ListenOptions, build_listener
    from tests.conftest import REPO_ROOT

    settings = load_settings(root=REPO_ROOT, load_env_file=False)
    for host in ("192.168.1.20", "brain.example.com", "0.0.0.0"):
        settings.profile.server.host = host
        with pytest.raises(ListenerUnavailableError, match="not a loopback address"):
            build_listener(settings, ListenOptions())


def test_writer_closes_the_socket_when_a_send_fails() -> None:
    """Review L8: a failed send ends the session (so it reconnects), it isn't swallowed."""
    import asyncio

    from sasha.listener.brain import writer

    class BrokenSocket:
        closed = False

        async def send(self, message: object) -> None:
            raise ConnectionResetError

        async def close(self) -> None:
            self.closed = True

    async def main() -> bool:
        ws = BrokenSocket()
        queue: asyncio.Queue[str | bytes] = asyncio.Queue()
        queue.put_nowait("x")
        await asyncio.wait_for(writer(ws, queue), 2)
        return ws.closed

    assert asyncio.run(main())
