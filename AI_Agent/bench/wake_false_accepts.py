"""False wakes from long background speech, and the real wakes they must not cost.

Synthetic Kokoro speech -> the listener's gate -> real Whisper (CPU int8) with the wake
hint -> the wake gate, as production runs it. Two sets:
- `wake`: the phrase with a command and the phrase alone (assistant.yaml forms);
- `background`: 5-8 s of talk with no phrase in it: one voice, a far voice in noise, two
  voices over each other, and segments that start mid-word (TV, people in the room).
Each row keeps the decoder's numbers: the segment's confidence, the phrase words'
probability (server/wake_evidence.py) and the no-hint decode's outcome. The summary gives
false accepts and missed wakes for the gate as it is (`gate`) and for each candidate
rule. Synthetic speech only; transcripts are printed. Results go to Data/results/.

Usage (from AI_Agent/):
  uv run python bench/wake_false_accepts.py [--quick] [--voices a,b] [--part wake|background]
  uv run python bench/wake_false_accepts.py --from FILE [FILE ...]   (the summary again)
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wake_synthetic import load, segments, synth

from sasha.config import load_settings
from sasha.server.wake import HITS, REJECTED_UNCONFIRMED, WakeGate, WakeMatcher
from sasha.server.wake_evidence import phrase_evidence

# Talk with no wake phrase and no "hey" in it, each 5-8 s when spoken.
BACKGROUND = [
    "And that brings us to the end of the first half, with the home side leading by two "
    "goals and the crowd on its feet.",
    "I was telling her yesterday that the new place on the corner does a really good "
    "breakfast, but you have to get there early.",
    "The minister said the new rules would come into force next month, although several "
    "groups have already asked for more time.",
    "So what we do now is add the onions, let them go soft for a few minutes, and then the "
    "tomatoes go in.",
    "Honestly I don't know why he keeps calling at this hour, it's always something that "
    "could have waited until the morning.",
    "Traffic on the main road is moving slowly this evening after an accident near the "
    "bridge, so give yourself extra time.",
    "She said she would send the photos tonight, so we can choose the ones for the album "
    "over the weekend.",
    "After the break we'll be talking to a doctor about sleep, and why so many of us find "
    "it hard to switch off at night.",
]
# (label, gain, SNR dB or None, other voices' gain or None, cut the first seconds,
# reversed, room reverb). Time-reversed speech stands in for talk in a language the
# decoder doesn't know. In a room (reverb) it is the regime of the real false wakes:
# averages of 0.3-0.45, and the decoder writes the hinted name (search, 2026-11-16).
CONDITIONS = [
    ("one", 1.0, None, None, 0.0, False, False),
    ("two", 1.0, None, 0.7, 0.0, False, False),
    ("babble", 0.5, 5.0, 0.9, 0.3, False, False),
    ("reversed", 1.0, 20.0, None, 0.0, True, False),
    ("room_talk", 0.5, 15.0, 0.6, 0.0, False, True),
    ("room_rev", 1.0, 10.0, None, 0.0, True, True),
    ("room_rev_two", 0.5, 5.0, 0.8, 0.3, True, True),
]
# The real wakes. The owner's "phrase, command" runs 3-5.5 s (review F1), so two long
# commands, all said a little slowly (WAKE_SPEED), make the segments as long as his.
COMMANDS = [
    "",
    ", what time is it",
    ", set a timer for ten minutes for the pasta please",
    ", turn on the kitchen light and the lamp in the hall",
]
WAKE_SPEED = 0.9
# (label, gain, SNR dB or None, room reverb)
WAKE_LEVELS = [
    ("clean", 1.0, None, False),
    ("quiet", 0.25, None, False),
    ("noisy", 1.0, 10.0, False),
    ("room", 0.5, 15.0, True),
]


def reverb(x: Any, rng: Any, rt60: float = 0.7) -> Any:
    """A made-up room: exponentially decaying noise as the impulse response."""
    n = int(rt60 * 16000)
    ir = rng.normal(0, 1, n) * np.exp(-6.9 * np.arange(n) / n)
    ir[0] = 1.0
    ir /= np.sqrt(np.sum(ir**2))
    size = x.size + n - 1
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)  # fast convolve
    return wet[: x.size + n // 2].astype(np.float32)


def background_audio(main: Any, others: list[Any], gain: float | None, cut: float) -> Any:
    """The main voice, cut to start mid-word, with the others 0.6 s apart over it."""
    x = main[int(cut * 16000) :]
    for k, o in enumerate(others if gain is not None else []):
        lag = np.concatenate([np.zeros(int(0.6 * (k + 1) * 16000), np.float32), o * gain])
        n = max(x.size, lag.size)
        x = np.pad(x, (0, n - x.size)) + np.pad(lag, (0, n - lag.size))
    return x


def decode(stt: Any, gate_args: tuple[Any, ...], seg: bytes, hint: str) -> dict[str, Any]:
    matcher: WakeMatcher = gate_args[0]
    heard = stt.transcribe(seg, 16000, hotwords=hint)
    d = WakeGate(*gate_args[:3]).decide(heard.text, heard.confidence)  # the gate before
    ev = phrase_evidence(heard.text, heard.words, matcher.phrase_tokens(heard.text))
    plain = stt.transcribe(seg, 16000)  # the same audio without the hint, in full
    opening = stt.transcribe(seg, 16000, opening=True).text  # as the recheck decodes it
    seconds = len(seg) / 32000
    gate = WakeGate(*gate_args)
    new = gate.decide(heard.text, heard.confidence, heard.words, seconds, opening)
    return {
        "text": heard.text,
        "seg_s": round(len(seg) / 32000, 2),
        "confidence": heard.confidence,
        "outcome": d.outcome,
        "outcome_new": new.outcome,
        "rechecked": new.rechecked or new.outcome == REJECTED_UNCONFIRMED,
        "opening_text": opening,
        "phrase_p": None if ev is None else ev.probability,
        "phrase_words": None if ev is None else list(ev.words),
        "phrase_start": None if ev is None else ev.start_s,
        "phrase_dur": None if ev is None else ev.duration_s,
        "plain_text": plain.text,
        "plain_outcome": matcher.match(plain.text)[0],
    }


Rule = Callable[[dict[str, Any]], bool]
FLOORS = (0.0, 0.30, 0.35, 0.40)  # min_command_confidence candidates
SURE = (0.0, 0.50, 0.55, 0.60)  # sure_command_confidence candidates (0: no recheck)
RECHECK_FROM_S = 4.0


def rules(matcher: WakeMatcher) -> dict[str, Rule]:
    """Candidates: does this row wake? `gate` is the gate before this change; `new_gate`
    the gate as configured now; `f…,s…` replay floor and recheck band on saved rows."""

    def hit(r: dict[str, Any]) -> bool:
        return r["outcome"] in HITS

    def bars(floor: float, sure: float) -> Rule:
        def rule(r: dict[str, Any]) -> bool:
            if r["outcome"] != "accepted_with_command":
                return hit(r)
            c = r["confidence"] or 0
            if c < floor:
                return False
            if c < sure and r["seg_s"] >= RECHECK_FROM_S:
                return matcher.opens_like_phrase(r.get("opening_text", r["plain_text"]))
            return True

        return rule

    out: dict[str, Rule] = {"gate": hit, "new_gate": lambda r: r.get("outcome_new") in HITS}
    out |= {f"f{f},s{t}": bars(f, t) for f in FLOORS for t in SURE}
    out["phrase_p>=0.3"] = lambda r: (
        hit(r) and (r["outcome"] != "accepted_with_command" or (r["phrase_p"] or 0) >= 0.3)
    )
    return out


def summary(rows: list[dict[str, Any]], matcher: WakeMatcher) -> dict[str, dict[str, int]]:
    wake = [r for r in rows if r["set"] == "wake"]
    bg = [r for r in rows if r["set"] == "background"]
    table: dict[str, dict[str, int]] = {}
    for name, rule in rules(matcher).items():
        table[name] = {
            "false_accepts": sum(rule(r) for r in bg),
            "missed_with_command": sum(not rule(r) for r in wake if r["cmd"]),
            "missed_alone": sum(not rule(r) for r in wake if not r["cmd"]),
        }
    print(f"\n{len(bg)} background segments, {len(wake)} wake samples")
    long_ = [r for r in wake if r["cmd"] and r["seg_s"] >= RECHECK_FROM_S]
    print(
        f"real wakes with a command: {sum(r['cmd'] for r in wake)}, of which "
        f"{len(long_)} are {RECHECK_FROM_S:g} s or longer and "
        f"{sum(bool(r.get('rechecked')) for r in wake)} were rechecked by the shipped gate"
    )
    print(f"{'rule':16} false_accepts missed_cmd missed_alone")
    for name, t in table.items():
        print(
            f"{name:16} {t['false_accepts']:13} {t['missed_with_command']:10}"
            f" {t['missed_alone']:12}"
        )
    return table


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--quick", action="store_true", help="two levels, fewer texts")
    p.add_argument("--voices", default="")
    p.add_argument("--part", choices=("all", "wake", "background"), default="all")
    p.add_argument("--from", dest="sources", nargs="+", help="summarise saved results only")
    a = p.parse_args()
    s = load_settings()
    ww, wake = s.assistant.wake_word, s.profile.wake
    name = ww.phrase
    matcher = WakeMatcher(name, ww.variants, wake.fillers, wake.continuations, ww.word_variants)
    if a.sources:
        rows = [r for f in a.sources for r in json.loads(Path(f).read_text(encoding="utf-8"))]
        summary([r for r in rows if "outcome" in r], matcher)
        return 0
    s, stt, tts, voices = load(a.quick, a.voices)
    gate_args = (
        matcher,
        wake.armed_window_s,
        wake.min_confidence,
        time.monotonic,
        wake.min_command_confidence,
        wake.sure_command_confidence,
        wake.recheck_from_s,
    )
    ph, rng, rows = tts._phonemize, np.random.default_rng(11), []
    forms = {"g2p": ph(name)} | {f"p{i}": v for i, v in enumerate(ww.test_pronunciations)}
    levels = WAKE_LEVELS[:2] if a.quick else WAKE_LEVELS
    wake_items = itertools.product(voices.items(), forms.items(), COMMANDS, levels)
    for (vname, voice), (form, phon), cmd, (label, gain, snr, room) in (
        wake_items if a.part != "background" else []
    ):
        x = synth(tts, phon + (ph(cmd) if cmd else ""), voice, WAKE_SPEED)
        segs = segments(reverb(x, rng) if room else x, gain, snr, rng)
        row = dict(set="wake", voice=vname, form=form, cmd=bool(cmd), cond=label)
        rows.append({**row, **(decode(stt, gate_args, segs[0], name) if segs else {})})
        print(row, rows[-1].get("outcome"), rows[-1].get("phrase_p"), flush=True)
    names = list(voices)
    texts = BACKGROUND[:4] if a.quick else BACKGROUND
    bg_items = itertools.product(voices.items(), texts, CONDITIONS)
    for i, ((vname, voice), text, cond) in enumerate(bg_items if a.part != "wake" else []):
        label, gain, snr, others_gain, cut, reverse, room = cond
        at = names.index(vname)
        x = synth(tts, ph(text), voice, 1.0)
        n_others = 2 if label in ("babble", "room_rev_two") else 1
        others = [
            synth(tts, ph(texts[(i + k) % len(texts)]), voices[names[(at + k) % len(names)]], 1.0)
            for k in range(1, n_others + 1)
            if others_gain is not None
        ]
        if reverse:
            x, others = x[::-1].copy(), [o[::-1].copy() for o in others]
        mixed = background_audio(x, others, others_gain, cut)
        for seg in segments(reverb(mixed, rng) if room else mixed, gain, snr, rng):
            rows.append(
                {"set": "background", "voice": vname, "cond": label, "item": i, "cmd": False}
                | decode(stt, gate_args, seg, name)
            )
            r = rows[-1]
            print(f"bg {vname} {label} {r['seg_s']}s {r['outcome']} {r['phrase_p']}", flush=True)
    out = s.paths.data_dir / "results"
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{a.part}-{a.voices.replace(',', '+') or 'all'}"
    path = out / f"wake_false_accepts-{tag}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {path}")
    summary([r for r in rows if "outcome" in r], matcher)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
