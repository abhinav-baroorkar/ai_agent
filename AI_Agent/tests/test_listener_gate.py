"""The listener's gate on synthetic audio (fake VAD), plus the real Silero model."""

from __future__ import annotations

import importlib.util

import pytest

np = pytest.importorskip("numpy")

from sasha.listener.gate import MAX_SPEECH_MS, Gate  # noqa: E402
from tests.listener_fakes import FakeVad, chunks  # noqa: E402


def run(gate: Gate, audio: list[bytes]) -> list[int]:
    """Feed chunks; return the speech_ms of each segment that came out."""
    out = []
    for chunk in audio:
        seg = gate.feed(chunk)
        if seg is not None:
            out.append(seg.speech_ms)
            assert len(seg.pcm) % 640 == 0
    return out


def test_one_utterance_after_silence_gives_one_segment() -> None:
    gate = Gate(FakeVad())
    audio = chunks("quiet", 1000) + chunks("speech", 1200) + chunks("quiet", 1000)
    segs = []
    for c in audio:
        if (s := gate.feed(c)) is not None:
            segs.append(s)
    assert len(segs) == 1
    seg = segs[0]
    assert 1150 <= seg.speech_ms <= 1250  # the VAD's 32 ms windows lag by up to a chunk
    # 1 s pre-roll + speech + 600 ms closing silence, all 20 ms chunks.
    assert abs(len(seg.pcm) // 640 - (50 + 60 + 30)) <= 2
    assert gate.stats.segments == 1 and gate.stats.drops == 0


def test_too_short_and_too_long_are_dropped() -> None:
    gate = Gate(FakeVad())
    assert run(gate, chunks("quiet", 500) + chunks("speech", 200) + chunks("quiet", 800)) == []
    assert gate.stats.too_short == 1
    long_speech = chunks("speech", MAX_SPEECH_MS + 1000)
    assert run(gate, long_speech + chunks("quiet", 800)) == []
    assert gate.stats.too_long == 1
    # After a long drop the gate works again.
    assert len(run(gate, chunks("quiet", 500) + chunks("speech", 600) + chunks("quiet", 800))) == 1


def test_too_long_drops_the_audio_at_once() -> None:
    gate = Gate(FakeVad())
    run(gate, chunks("quiet", 400) + chunks("speech", MAX_SPEECH_MS + 400))
    assert gate._seg is not None and gate._seg.too_long and gate._seg.chunks == []


def test_needs_300_ms_of_silence_before_speech() -> None:
    gate = Gate(FakeVad())
    # Mic opens mid-sentence: speech at once, no silence first -> nothing starts.
    assert run(gate, chunks("speech", 1000)) == []
    assert gate._seg is None
    gate.reset()
    assert run(gate, chunks("quiet", 200) + chunks("speech", 800) + chunks("quiet", 800)) == []
    gate.reset()
    assert run(gate, chunks("quiet", 300) + chunks("speech", 800) + chunks("quiet", 800)) == [800]


def test_loud_sound_that_vad_rejects_is_not_speech() -> None:
    gate = Gate(FakeVad(say=lambda w: 0.1))  # e.g. music or a door
    assert run(gate, chunks("quiet", 500) + chunks("speech", 1000) + chunks("quiet", 800)) == []


def test_speech_must_be_10_db_over_the_noise_floor() -> None:
    gate = Gate(FakeVad(say=lambda w: 0.9 if np.abs(w).mean() > 0.0001 else 0.0))
    # A loud room: noise near -30 dBFS for 5 s sets the floor, VAD thinks it's "speech"
    # but it is never 10 dB over the floor, so nothing is sent.
    noisy = chunks("noise", 5000, amplitude=1000)
    gate2 = Gate(FakeVad(say=lambda w: 0.0))
    for c in noisy:
        gate2.feed(c)
    floor = gate2.noise_floor_db
    assert -35 < floor < -25
    assert run(gate, noisy) == []


def test_long_talk_does_not_raise_the_noise_floor() -> None:
    gate = Gate(FakeVad())
    run(gate, chunks("quiet", 2000))
    quiet_floor = gate.noise_floor_db
    run(gate, chunks("speech", 20000))  # 20 s of talk (dropped as too long)
    assert gate.noise_floor_db == pytest.approx(quiet_floor, abs=3)


def test_reset_needs_fresh_silence() -> None:
    gate = Gate(FakeVad())
    run(gate, chunks("quiet", 1000))
    gate.reset()
    assert run(gate, chunks("speech", 600) + chunks("quiet", 800)) == []


@pytest.mark.skipif(importlib.util.find_spec("faster_whisper") is None, reason="voice extra")
def test_real_silero_ignores_silence_and_noise() -> None:
    from sasha.listener.vad import SileroVad

    gate = Gate(SileroVad())
    assert run(gate, chunks("quiet", 2000) + chunks("noise", 3000, amplitude=2000)) == []
    probs = [gate.vad.probability(np.zeros(512, dtype=np.float32)) for _ in range(5)]
    assert max(probs) < 0.5


def test_a_short_answer_passes_only_with_the_answer_minimum() -> None:
    """A crisp "yes" (~0.3 s) is too short for a wake segment but fine as an answer."""
    from sasha.listener.gate import ANSWER_MIN_SPEECH_MS, MIN_SPEECH_MS

    yes = chunks("quiet", 500) + chunks("speech", 300) + chunks("quiet", 800)
    gate = Gate(FakeVad())
    assert gate.min_speech_ms == MIN_SPEECH_MS
    assert run(gate, yes) == []
    gate.min_speech_ms = ANSWER_MIN_SPEECH_MS
    assert run(gate, yes) == [300]
    assert run(gate, chunks("speech", 100) + chunks("quiet", 800)) == []  # a click still isn't
