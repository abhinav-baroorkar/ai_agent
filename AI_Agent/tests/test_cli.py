from __future__ import annotations

from pathlib import Path

from sasha.cli import build_parser, run_chat
from sasha.config import Settings
from tests.test_orchestrator import ScriptedLLM, _assistant, _resp


def test_chat_loop_commands(settings: Settings, tmp_path: Path) -> None:
    a = _assistant(settings, ScriptedLLM([_resp("Hello!")]), tmp_path)
    inputs = iter(["", "/tools", "hi", "/reset", "/quit"])
    out: list[str] = []
    assert run_chat(a, read=lambda _: next(inputs), write=out.append) == 0
    assert out[0].startswith(settings.assistant.name)
    assert "get_state" in out
    assert f"{settings.assistant.name}> Hello!" in out
    assert "(conversation cleared)" in out


def test_chat_exits_on_eof(settings: Settings, tmp_path: Path) -> None:
    a = _assistant(settings, ScriptedLLM([]), tmp_path)

    def eof(_: str) -> str:
        raise EOFError

    assert run_chat(a, read=eof, write=lambda _: None) == 0


def test_parser() -> None:
    args = build_parser().parse_args(["--profile", "jetson-orin-nano", "chat", "--timing"])
    assert args.profile == "jetson-orin-nano" and args.timing and args.command == "chat"
