"""The wake change-request bench (bench/wake_request_phrases.yaml): part of the gate for
device control by voice. 0 change requests missed; questions and chat still stream."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sasha.config import ReplyLines
from sasha.server.wake_truth import requests_change

BENCH = Path(__file__).resolve().parents[1] / "bench" / "wake_request_phrases.yaml"
PHRASES = yaml.safe_load(BENCH.read_text(encoding="utf-8"))


def test_bench_has_about_forty_phrases() -> None:
    assert len(PHRASES["request"]) + len(PHRASES["stream"]) >= 40


@pytest.mark.parametrize("command", PHRASES["request"])
def test_no_change_request_is_missed(command: str) -> None:
    assert requests_change(command, ReplyLines()), command


@pytest.mark.parametrize("command", PHRASES["stream"])
def test_questions_and_chat_still_stream(command: str) -> None:
    assert not requests_change(command, ReplyLines()), command


def test_bare_scene_name_is_a_request() -> None:
    """Scene names come from the house's device list at run time ("goodnight")."""
    scenes = lambda: ["Goodnight", "Movie night"]  # noqa: E731
    assert requests_change("goodnight", ReplyLines(), scenes)
    assert requests_change("movie night!", ReplyLines(), scenes)
    assert not requests_change("goodnight", ReplyLines(), lambda: [])
    assert not requests_change("good morning", ReplyLines(), scenes)


def test_armed_name_then_a_bare_state_word() -> None:
    """'<name>.' then 'off.': the follow-up command is just the state word."""
    for command in ("off.", "On!", "lights out", "all down"):
        assert requests_change(command, ReplyLines()), command
