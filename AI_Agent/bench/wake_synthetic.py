"""Synthetic wake check: Kokoro voices -> the listener's gate -> real Whisper (CPU) -> matcher.

No microphone, no server, no GPU (STT is forced to CPU int8). The wake phrase and its
spellings come from assistant.yaml. Two sets:
- `wake`: the name alone and "name, command", said as the phonemizer says it and as
  `wake_word.test_pronunciations` list, several voices, speeds, levels and noise;
- `ordinary`: sentences without the name, plus two that mention it (must not wake).
Counts outcomes; prints transcripts (synthetic speech only, never a person's). Results
go to Data/results/ as JSON.

Voices: every Kokoro voice .bin in the HF cache (fetch extra ones, e.g. the Hindi-accent
hf_alpha, hf_beta, hm_omega, hm_psi, with huggingface_hub at the pinned revision).

Usage (from AI_Agent/):
  uv run python bench/wake_synthetic.py [--set wake|ordinary] [--quick] [--voices a,b]
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from sasha.config import load_settings
from sasha.listener.gate import CHUNK_BYTES, Gate
from sasha.listener.vad import SileroVad
from sasha.pipeline.voice import build_stt, build_tts
from sasha.server.wake import HITS, WakeGate, WakeMatcher

COMMANDS = ["", ", what time is it", ", turn on the kitchen light"]
ORDINARY = [
    "What time is it?",
    "Turn on the kitchen light.",
    "I think we should leave soon.",
    "Can you pass me the salt?",
    "The meeting starts at nine tomorrow.",
    "Natasha called this morning.",
    "Did you see the match yesterday?",
    "Let's order some food.",
    "Shall we go for a walk?",
    "My sister is coming over tonight.",
    "Hello, how are you doing?",
    "Okay, sounds good.",
    "Yes.",
    "No, thanks.",
    "Hmm.",
]
MENTIONS = ["{name} said she would call.", "I told {name} about it."]  # must not wake
LEVELS = [(1.0, None), (0.25, None), (1.0, 20.0), (1.0, 10.0)]  # (gain, SNR dB or None)


def load(quick: bool, voice_filter: str) -> tuple[Any, Any, Any, dict[str, Any]]:
    s = load_settings()
    stt_cfg = s.profile.stt.model_copy(
        update={"device": "cpu", "compute_type": "int8", "cpu_threads": 4}
    )
    s = s.model_copy(update={"profile": s.profile.model_copy(update={"stt": stt_cfg})})
    stt, tts = build_stt(s), build_tts(s)
    hub = Path(os.environ.get("HF_HOME", s.paths.root / "Dependencies" / "hf")) / "hub"
    folders = sorted(hub.glob("models--onnx-community--Kokoro-82M-v1.0-ONNX/snapshots/*/voices"))
    voices = {
        v.stem: np.fromfile(v, dtype=np.float32).reshape(-1, 1, 256)
        for v in sorted(folders[-1].glob("*.bin"))
        if not voice_filter or v.stem in voice_filter.split(",")
    }
    return s, stt, tts, voices


def synth(tts: Any, phonemes: str, voice: Any, speed: float) -> Any:
    audio, rate = tts._kokoro.create(phonemes, voice=voice, speed=speed, is_phonemes=True)
    t = np.arange(0, len(audio) / rate, 1 / 16000)
    return np.interp(t, np.arange(len(audio)) / rate, audio).astype(np.float32)


def segments(x: Any, gain: float, snr: float | None, rng: Any) -> list[bytes]:
    pad = np.zeros(int(0.8 * 16000), np.float32)
    speech = x * gain * 0.6
    sig = np.concatenate([pad, speech, pad, pad])
    power = float(np.mean(speech**2)) or 1e-9
    sd = np.sqrt(power / 10 ** (snr / 10)) if snr is not None else 3e-4  # or a quiet room
    sig = sig + rng.normal(0, sd, sig.size).astype(np.float32)
    pcm = (np.clip(sig, -1, 1) * 32767).astype("<i2").tobytes()
    gate, out = Gate(SileroVad()), []
    for i in range(0, len(pcm) - CHUNK_BYTES + 1, CHUNK_BYTES):
        if (seg := gate.feed(pcm[i : i + CHUNK_BYTES])) is not None:
            out.append(seg.pcm)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--set", choices=("wake", "ordinary"), default="wake")
    p.add_argument("--quick", action="store_true", help="one speed, clean audio only")
    p.add_argument("--voices", default="")
    a = p.parse_args()
    s, stt, tts, voices = load(a.quick, a.voices)
    name, wake = s.assistant.wake_word.phrase, s.profile.wake
    matcher = WakeMatcher(name, s.assistant.wake_word.variants, wake.fillers, wake.continuations)
    ph = tts._phonemize
    if a.set == "wake":
        # the phonemizer's form, plus the ways people say it from assistant.yaml
        # (wake_word.test_pronunciations, phonemes)
        forms = {"g2p": ph(name)}
        forms |= {f"p{i}": v for i, v in enumerate(s.assistant.wake_word.test_pronunciations)}
        items = [
            (f, phon + (ph(c) if c else ""), bool(c))
            for (f, phon), c in itertools.product(forms.items(), COMMANDS)
        ]
    else:
        texts = ORDINARY + [m.format(name=name) for m in MENTIONS]
        items = [(f"o{i}", ph(t), False) for i, t in enumerate(texts)]
    speeds, levels = ([1.0], LEVELS[:1]) if a.quick else ([0.85, 1.0, 1.2], LEVELS)
    rng, rows = np.random.default_rng(7), []
    for (vname, voice), (form, phon, cmd), speed, (gain, snr) in itertools.product(
        voices.items(), items, speeds, levels
    ):
        segs = segments(synth(tts, phon, voice, speed), gain, snr, rng)
        row: dict[str, Any] = dict(voice=vname, form=form, cmd=cmd, speed=speed, gain=gain, snr=snr)
        if not segs:
            rows.append({**row, "outcome": "gate_dropped"})
            continue
        t0 = time.perf_counter()
        heard = stt.transcribe(segs[0], 16000, hotwords=name)
        d = WakeGate(matcher, wake.armed_window_s, wake.min_confidence).decide(
            heard.text, heard.confidence
        )
        rows.append(
            {
                **row,
                "text": heard.text,
                "confidence": heard.confidence,
                "outcome": d.outcome,
                "stt_ms": round((time.perf_counter() - t0) * 1000),
            }
        )
        print(
            f"{vname:9} {form:8} cmd={int(cmd)} sp={speed} g={gain} snr={snr} "
            f"{heard.confidence or 0:.2f} {d.outcome:22} {heard.text!r}",
            flush=True,
        )
    counts = Counter(r["outcome"] for r in rows)
    hits = sum(n for o, n in counts.items() if o in HITS)
    print(f"\n{a.set}: {len(rows)} samples, {hits} woke; {dict(counts)}")
    out = s.paths.data_dir / "results"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"wake_synthetic-{a.set}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
