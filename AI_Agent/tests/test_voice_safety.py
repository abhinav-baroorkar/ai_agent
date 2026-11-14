"""Limits, log privacy and the derived voice file. Needs numpy (the `voice` extra), no models."""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sasha.config import STTConfig, TTSConfig
from tests.conftest import REPO_ROOT

np = pytest.importorskip("numpy")

from sasha.pipeline.stt_whisper import FasterWhisperSTT  # noqa: E402
from sasha.pipeline.tts_kokoro import (  # noqa: E402
    STYLE_DIM,
    KokoroTTS,
    SpeechError,
    _read_npz,
    cap_text,
    voices_npz,
)

SECRET = "my bank PIN is 4321"


def test_cap_text_cuts_at_sentence_end() -> None:
    text = "First sentence here. Second sentence here. Third one is here."
    assert cap_text(text, 1000) == text
    assert cap_text(text, 45) == "First sentence here. Second sentence here."
    assert cap_text("word " * 50, 23) == "word word word word"


def _bare_stt(max_seconds: float) -> FasterWhisperSTT:
    stt = FasterWhisperSTT.__new__(FasterWhisperSTT)
    stt.config = STTConfig(max_seconds=max_seconds)
    stt.languages = ["en"]
    return stt


def test_stt_refuses_overlong_audio() -> None:
    stt = _bare_stt(max_seconds=1.0)
    with pytest.raises(ValueError, match="limit"):
        stt.transcribe(b"\x00\x00" * 16000 * 2, 16000)
    with pytest.raises(ValueError, match="16000"):
        stt.transcribe(b"\x00\x00", 8000)


class _Tokenizer:
    def phonemize(self, text: str, lang: str) -> str:
        raise ValueError(f"No phonemes of {text!r} are in the model vocabulary")


class _Kokoro:
    def __init__(self, fail_with: type[Exception]) -> None:
        self.tokenizer = _Tokenizer()
        self.fail_with = fail_with

    def create(self, phonemes: str, **kwargs: Any) -> Any:
        raise self.fail_with(f"failed on {phonemes!r}")


def _bare_tts(kokoro: Any, max_chars: int = 4000) -> KokoroTTS:
    tts = KokoroTTS.__new__(KokoroTTS)
    tts.config = TTSConfig(max_chars=max_chars)
    tts.voice_id, tts.speed, tts.lang = "af_heart", 1.0, "en-us"
    tts._g2p_lock = threading.Lock()
    tts._kokoro = kokoro
    return tts


def test_tts_logs_never_contain_the_text(caplog: pytest.LogCaptureFixture) -> None:
    tts = _bare_tts(_Kokoro(RuntimeError), max_chars=30)
    with caplog.at_level(logging.DEBUG):
        clips = list(tts.stream(f"{SECRET}. And then some more words here.", "en"))
    assert clips == []
    assert "skipped" in caplog.text and "cut from" in caplog.text
    assert SECRET not in caplog.text and "4321" not in caplog.text


def test_tts_errors_never_contain_the_text() -> None:
    tts = _bare_tts(_Kokoro(RuntimeError))
    tts._phonemize = lambda text: "phonemes of " + text  # type: ignore[method-assign]
    stream: Iterator[Any] = tts.stream(SECRET, "en")
    with pytest.raises(SpeechError) as info:
        next(stream)
    assert "4321" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


@pytest.fixture
def voice_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A scratch HF cache inside the project's git-ignored Data/, removed afterwards."""
    base = REPO_ROOT / "Data" / "test-voice-npz"
    shutil.rmtree(base, ignore_errors=True)
    (base / "hub").mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(base / "hub"))
    yield base
    shutil.rmtree(base, ignore_errors=True)


def test_voices_npz_is_built_validated_and_rebuilt(voice_dir: Path) -> None:
    style = np.arange(4 * STYLE_DIM, dtype="<f4")
    voice_bin = voice_dir / "af_test.bin"
    style.tofile(voice_bin)
    out = voices_npz(voice_bin, "af_test", "a" * 40, REPO_ROOT)
    assert out.parent == voice_dir / "derived"
    with np.load(out, allow_pickle=False) as data:
        assert data["af_test"].shape == (4, 1, STYLE_DIM)
        assert data["af_test"].dtype == np.float32
    assert [p.name for p in out.parent.iterdir()] == [out.name]  # no temp files left

    out.write_bytes(b"not an npz")  # corrupt: rebuilt on next use
    assert voices_npz(voice_bin, "af_test", "a" * 40, REPO_ROOT) == out
    assert _read_npz(out, "af_test")


def test_voices_npz_rejects_pickles_and_bad_shapes(voice_dir: Path) -> None:
    pickled = voice_dir / "pickled.npz"
    np.savez(pickled, af_test=np.array([{"x": 1}], dtype=object))
    assert not _read_npz(pickled, "af_test")
    wrong = voice_dir / "wrong.npz"
    np.savez(wrong, af_test=np.zeros((4, 2, STYLE_DIM), dtype=np.float32))
    assert not _read_npz(wrong, "af_test")

    bad_bin = voice_dir / "bad.bin"
    np.zeros(STYLE_DIM + 3, dtype="<f4").tofile(bad_bin)
    with pytest.raises(SpeechError, match="unexpected size"):
        voices_npz(bad_bin, "af_bad", "b" * 40, REPO_ROOT)
