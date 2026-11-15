"""The wake word listener's on-screen sign: words, icons, sounds and the fake."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from sasha.config import load_settings
from sasha.listener.indicator import (
    MIC_OPEN,
    STATES,
    FakeIndicator,
    Persona,
    State,
    describe,
    earcon,
)
from sasha.listener.indicator.icons import SIZES, glyph, pixmaps
from tests.conftest import REPO_ROOT
from tests.indicator_fakes import WHO


# --- states, words and icons -------------------------------------------------------
def test_every_state_has_words_and_icons_at_every_size() -> None:
    for state in STATES:
        assert WHO.name in describe(state, WHO)
        sizes = pixmaps(state)
        assert [p[0] for p in sizes] == list(SIZES)
        for width, height, data in sizes:
            assert len(data) == width * height * 4
            alphas = data[0::4]
            assert max(alphas) == 255 and min(alphas) == 0  # something drawn, on a clear ground


def test_all_icons_differ_and_22px_is_one_of_the_sizes() -> None:
    at_22 = {state: next(p[2] for p in pixmaps(state) if p[0] == 22) for state in STATES}
    assert len(set(at_22.values())) == len(STATES)


def _lit(data: bytes, size: int, x: float, y: float) -> tuple[int, int, int, int]:
    i = (int(y * size) * size + int(x * size)) * 4
    return struct.unpack("4B", data[i : i + 4])  # type: ignore[return-value]


def test_live_and_crossed_out_microphones_are_clearly_different() -> None:
    """Paused and offline carry a diagonal stroke; the mic-open states never do."""
    size = 48
    on_stroke = [(0.2, 0.19), (0.8, 0.81)]  # on the stroke, away from the microphone
    beside = (0.2, 0.81)  # the mirror corner: empty unless the whole square is filled
    for state in STATES:
        data = next(p[2] for p in pixmaps(state) if p[0] == size)
        struck = all(_lit(data, size, x, y)[0] == 255 for x, y in on_stroke)
        struck = struck and _lit(data, size, *beside)[0] == 0
        assert struck == (state in ("paused", "offline")), state
    assert {"waiting", "listening", "debug"} == MIC_OPEN


def test_waiting_and_speaking_cannot_be_confused_at_22px() -> None:
    """Speaking is a filled disc; waiting is a bare microphone. Compare how much is painted."""

    def painted(state: State) -> float:
        data = next(p[2] for p in pixmaps(state) if p[0] == 22)
        return sum(1 for a in data[0::4] if a > 128) / (22 * 22)

    assert painted("speaking") > 0.7  # a disc fills most of the square
    assert painted("waiting") < 0.5  # a glyph does not
    corner = _lit(next(p[2] for p in pixmaps("speaking") if p[0] == 48), 48, 0.12, 0.55)
    assert corner[0] == 255 and corner[1] > 200  # amber disc where "waiting" is empty
    assert _lit(next(p[2] for p in pixmaps("waiting") if p[0] == 48), 48, 0.12, 0.55)[0] == 0


def test_state_colours_follow_the_window() -> None:
    def body(state: str) -> tuple[int, int, int]:
        a, r, g, b = _lit(next(p[2] for p in pixmaps(state) if p[0] == 48), 48, 0.5, 0.36)  # type: ignore[arg-type]
        assert a == 255
        return r, g, b

    r, g, b = body("waiting")
    assert r > 200 and g > 150 and b < 140  # amber
    r, g, b = body("listening")
    assert g > r and b > r  # teal
    r, g, b = body("thinking")
    assert b > g and b > 200  # violet
    r, g, b = body("debug")
    assert r > 230 and g < 110 and b < 110  # warning red
    r, g, b = body("offline")
    assert abs(r - g) < 20 and abs(g - b) < 20  # grey


def test_words_come_from_config_not_from_code() -> None:
    assistant = load_settings(root=REPO_ROOT, load_env_file=False).assistant
    who = Persona(assistant.name, assistant.wake_word.phrase)
    assert assistant.wake_word.phrase in describe("waiting", who)
    assert "microphone is on" in describe("waiting", who)
    assert "microphone is off" in describe("paused", who)
    assert "SAVING AUDIO" in describe("debug", who)
    for path in (REPO_ROOT / "AI_Agent/src/sasha/listener/indicator").glob("*.py"):
        assert assistant.name not in path.read_text(encoding="utf-8")


def test_no_icon_or_sound_files_are_shipped() -> None:
    files = [
        p
        for p in Path(REPO_ROOT / "AI_Agent/src/sasha/listener/indicator").rglob("*")
        if p.is_file()
    ]
    assert files and all(p.suffix == ".py" for p in files if "__pycache__" not in p.parts)


def test_unknown_state_is_refused() -> None:
    with pytest.raises(ValueError):
        glyph("sleeping")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        FakeIndicator().set_state("sleeping")  # type: ignore[arg-type]


# --- earcons -------------------------------------------------------------------------
def test_earcons_are_short_soft_and_distinct() -> None:
    wake, armed = earcon("wake"), earcon("armed")
    assert wake.pcm != armed.pcm
    for sound in (wake, armed):
        samples = struct.unpack(f"<{len(sound.pcm) // 2}h", sound.pcm)
        assert sound.sample_rate == 24000
        assert 0.1 < len(samples) / sound.sample_rate < 0.25
        assert 0.15 * 32767 < max(abs(s) for s in samples) < 0.3 * 32767  # audible, never loud
        assert (
            abs(samples[0]) < 200 and abs(samples[-1]) < 200
        )  # starts and ends in silence: no click
    with pytest.raises(ValueError):
        earcon("boom")  # type: ignore[arg-type]


def test_fake_indicator_behaves_like_the_real_interface() -> None:
    fake = FakeIndicator()
    events: list[str] = []
    assert fake.available() is False  # not started yet
    fake.start(lambda: events.append("toggle"), lambda: events.append("lost"))
    assert fake.available() is True
    fake.set_state("waiting")
    fake.click()
    fake.lose()
    assert events == ["toggle", "lost"] and fake.available() is False and fake.state == "waiting"
    assert fake.earcon("wake").sample_rate == 24000
    fake.stop()
    assert fake.available() is False
