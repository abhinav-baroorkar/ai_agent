"""Speech stages: fakes, sentence splitting, config and loader errors. No models needed."""

from __future__ import annotations

from pathlib import Path

import pytest

from sasha.config import STTConfig, TTSConfig, load_settings
from sasha.pipeline.fakes import FakeSTT, FakeTTS
from sasha.pipeline.model_cache import hf_cache_dir
from sasha.pipeline.stt import SpeechToText
from sasha.pipeline.tts import SpeechAudio, TextToSpeech, split_sentences
from sasha.pipeline.voice import VoiceUnavailableError, build_stt, build_tts
from tests.conftest import REPO_ROOT

# Inside the project (git-ignored Data/), never the system temp dir. Never created.
NO_CACHE = REPO_ROOT / "Data" / "test-no-such-hf-cache"


def test_fakes_satisfy_protocols() -> None:
    stt: SpeechToText = FakeSTT()
    tts: TextToSpeech = FakeTTS()
    assert stt.model_version() == "fake-stt"
    assert tts.model_version() == "fake-tts"


def test_fake_stt_returns_text_and_records_call() -> None:
    stt = FakeSTT(text="hello", language="en")
    result = stt.transcribe(b"\x00\x00" * 1600, 16000)
    assert (result.text, result.language, result.confidence) == ("hello", "en", 1.0)
    assert stt.calls == [(3200, 16000)]
    assert stt.transcribe(b"", 16000).text == ""


def test_fake_tts_streams_one_clip_per_sentence() -> None:
    tts = FakeTTS(sample_rate=24000, ms_per_char=10)
    clips = list(tts.stream("The light is on now. Anything else for you?", "en"))
    assert len(clips) == 2
    assert all(isinstance(c, SpeechAudio) and c.sample_rate == 24000 for c in clips)
    assert len(clips[0].pcm) == 2 * 24000 * 10 * len("The light is on now.") // 1000
    whole = tts.synthesize("The light is on now. Anything else for you?", "en")
    assert whole.pcm == b"".join(c.pcm for c in clips)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("Hello there.", ["Hello there."]),
        ("It is on. It is off!  Really?", ["It is on. It is off! Really?"]),  # short tail joins
        ("Lights are on. Do you want the fan too?", ["Lights are on.", "Do you want the fan too?"]),
        ("OK. The bedroom light is on.", ["OK. The bedroom light is on."]),
        ("Line one here\nline two here", ["Line one here", "line two here"]),
        ("दिवा चालू केला आहे। आणखी काही हवे आहे का?", ["दिवा चालू केला आहे।", "आणखी काही हवे आहे का?"]),
        ("Version 2.5 is out now.", ["Version 2.5 is out now."]),
        ("Done. Ok.", ["Done. Ok."]),
    ],
)
def test_split_sentences(text: str, expected: list[str]) -> None:
    assert split_sentences(text) == expected


def test_profiles_have_voice_config() -> None:
    for profile in ("pc", "jetson-orin-nano"):
        s = load_settings(profile=profile, root=REPO_ROOT, load_env_file=False)
        assert s.profile.stt.backend == "faster-whisper"
        assert s.profile.tts.backend == "kokoro"
        assert s.profile.tts.device == "cpu"
    s = load_settings(root=REPO_ROOT, load_env_file=False)
    assert s.assistant.voice.en_voice
    assert 0.5 <= s.assistant.voice.speed <= 2.0


def test_voice_config_defaults_keep_old_profiles_valid() -> None:
    assert STTConfig().model == "small"
    assert TTSConfig().revision  # pinned, so a moved upstream file can't change the voice


def test_hf_cache_dir_stays_in_project(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path("/project")  # pure path arithmetic, nothing is written
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    assert hf_cache_dir(root) == root / "Dependencies" / "hf" / "hub"
    monkeypatch.setenv("HF_HOME", "/project/Dependencies/hf2")
    assert hf_cache_dir(root) == Path("/project/Dependencies/hf2/hub")
    monkeypatch.setenv("HF_HUB_CACHE", "/project/hub3")
    assert hf_cache_dir(root) == Path("/project/hub3")


def test_build_stages_fail_cleanly_without_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the extra (CI) or without downloaded weights: a clear error, no download."""
    monkeypatch.setenv("HF_HUB_CACHE", str(NO_CACHE))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    settings = load_settings(root=REPO_ROOT, load_env_file=False)
    settings.profile.stt.device = "cpu"
    for build in (build_stt, build_tts):
        with pytest.raises(VoiceUnavailableError, match=r"voice"):
            build(settings)
    assert not NO_CACHE.exists()


def test_pcm_conversions_round_trip() -> None:
    np = pytest.importorskip("numpy")
    from sasha.pipeline.stt_whisper import pcm16_to_float
    from sasha.pipeline.tts_kokoro import float_to_pcm16, kokoro_lang

    samples = np.array([0.0, 0.5, -0.5, 1.5, -1.5], dtype=np.float32)
    back = pcm16_to_float(float_to_pcm16(samples))
    assert np.allclose(back, [0.0, 0.5, -0.5, 1.0, -1.0], atol=1e-3)
    with pytest.raises(ValueError):
        pcm16_to_float(b"\x00")
    assert kokoro_lang("af_heart") == "en-us"
    assert kokoro_lang("bf_emma") == "en-gb"
