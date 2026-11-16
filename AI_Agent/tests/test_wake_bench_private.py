"""Review X1: a private name given to the wake bench never leaves in clear.

The bench's real `main()` runs with a fake speech-to-text that writes the name back, so
the name would be printed and saved if anything let it through. Checked: stdout, the
saved JSON, and the command line (it can't be given there any more).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("numpy")

from sasha.config import load_settings
from sasha.pipeline.stt import Transcript
from tests.conftest import REPO_ROOT

SENTINEL = "Zorvexa"  # a made-up name


def bench() -> Any:
    path = REPO_ROOT / "AI_Agent/bench/wake_synthetic.py"
    spec = importlib.util.spec_from_file_location("wake_synthetic_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTTS:
    def _phonemize(self, text: str) -> str:
        return text


class FakeSTT:
    """Writes back what was 'said', so a leak would show."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def transcribe(self, audio: Any, rate: int, hotwords: str | None = None) -> Transcript:
        said = self.texts.pop(0) if self.texts else f"hey {SENTINEL.lower()}"
        return Transcript(said, "en", 0.5)


@pytest.mark.parametrize("source", ["file", "env"])
def test_a_private_name_is_never_printed_or_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    b = bench()
    s = load_settings()
    s = s.model_copy(update={"paths": s.paths.model_copy(update={"data_dir": tmp_path})})
    if source == "file":
        (tmp_path / "private").mkdir()
        (tmp_path / "private" / "soundalikes.txt").write_text(f"{SENTINEL}\n")
        monkeypatch.delenv(b.PRIVATE_ENV, raising=False)
    else:
        monkeypatch.setenv(b.PRIVATE_ENV, SENTINEL)
    stt = FakeSTT()
    near = SENTINEL[:-1] + "ah"  # a near-spelling: redaction alone would miss it
    stt.texts = [f"I told {SENTINEL} about it.", f"Hey {near}, turn on the light."]
    monkeypatch.setattr(b, "load", lambda quick, voices: (s, stt, FakeTTS(), {"v1": None}))
    monkeypatch.setattr(b, "synth", lambda tts, phon, voice, speed: phon)
    monkeypatch.setattr(b, "segments", lambda x, gain, snr, rng: [b"\0\0"])
    monkeypatch.setattr(sys, "argv", ["wake_synthetic.py", "--set", "extra", "--quick"])
    assert b.main() == 0
    out = capsys.readouterr().out
    saved = next((tmp_path / "results").glob("wake_synthetic-extra-*.json")).read_text()
    for leak in (SENTINEL.lower(), near.lower()):
        assert leak not in out.lower() and leak not in saved.lower()
    rows = json.loads(saved)
    assert len(rows) == 4
    assert all("text" not in r for r in rows)  # no transcript at all, not even redacted
    assert "name_written=True" in out


def test_the_name_cannot_come_from_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    b = bench()
    monkeypatch.setattr(sys, "argv", ["wake_synthetic.py", "--extra-soundalikes", SENTINEL])
    monkeypatch.setattr(b, "load", lambda quick, voices: pytest.fail("must not get this far"))
    with pytest.raises(SystemExit):
        b.main()


def test_redaction_is_whole_word_and_any_case() -> None:
    b = bench()
    assert b.redact("hey ZORVEXA and zorvexa's", [SENTINEL]) == "hey <extra-1> and <extra-1>'s"
    assert b.redact("Zorvexas", [SENTINEL]) == "Zorvexas"  # another word
    assert b.extra_names(Path("/nonexistent"), {b.PRIVATE_ENV: " A, B\n#c\nA"}) == ["A", "B"]
