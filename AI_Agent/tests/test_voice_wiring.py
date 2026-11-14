"""`load_voice_stages`: real stages when they build, None plus a plain warning otherwise."""

from __future__ import annotations

import pytest

from sasha.config import Settings
from sasha.pipeline.fakes import FakeSTT, FakeTTS
from sasha.pipeline.voice import VoiceUnavailableError
from sasha.server import voice as server_voice
from sasha.server.voice import VoiceStages, load_voice_stages, tts_languages


def _patch(monkeypatch: pytest.MonkeyPatch, stt: object, tts: object) -> None:
    def make(result: object) -> object:
        def build(settings: Settings) -> object:
            if isinstance(result, BaseException):
                raise result
            return result

        return build

    monkeypatch.setattr(server_voice, "build_stt", make(stt))
    monkeypatch.setattr(server_voice, "build_tts", make(tts))


def test_both_stages_load(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    stt, tts = FakeSTT(), FakeTTS()
    _patch(monkeypatch, stt, tts)
    assert load_voice_stages(settings) == (stt, tts, [])


def test_missing_extra_gives_plain_warnings(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    missing = VoiceUnavailableError("Voice packages not installed (numpy). Run: uv sync")
    _patch(monkeypatch, missing, missing)
    stt, tts, warnings = load_voice_stages(settings)
    assert stt is None and tts is None
    assert warnings == [
        "Voice speech-to-text is off: Voice packages not installed (numpy). Run: uv sync",
        "Voice text-to-speech is off: Voice packages not installed (numpy). Run: uv sync",
    ]


def test_unexpected_errors_never_escape(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    tts = FakeTTS()
    _patch(monkeypatch, RuntimeError("secret details /path/x"), tts)
    stt, got_tts, warnings = load_voice_stages(settings)
    assert stt is None and got_tts is tts
    assert warnings == ["Voice speech-to-text is off: failed to load (RuntimeError)"]


def test_languages_come_straight_from_the_stage() -> None:
    assert tts_languages(None) == []
    assert tts_languages(FakeTTS(languages=("en", "mr"))) == ["en", "mr"]
    assert VoiceStages(tts=FakeTTS()).languages == ["en"]


def test_real_build_without_models_is_off_not_raised(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The real builders, with an empty model cache: both off, the server still starts."""
    from tests.conftest import REPO_ROOT

    monkeypatch.setenv("HF_HUB_CACHE", str(REPO_ROOT / "Data" / "test-no-such-hf-cache"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    settings.profile.stt.device = "cpu"
    stt, tts, warnings = load_voice_stages(settings)
    assert stt is None and tts is None
    assert len(warnings) == 2 and all(w.startswith("Voice ") for w in warnings)
