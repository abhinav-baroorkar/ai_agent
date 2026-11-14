"""Drive spoken turns against a running brain server (`sasha serve`) and time them.

The "spoken command" is synthesised locally with the TTS stage (no recordings), resampled
to 16 kHz and streamed in real time as 20 ms frames, like a microphone. All timings are
measured from the moment `audio_stop` is sent (the end of speech):

  transcript       when the brain says what it heard
  first_text       first `reply_delta`
  first_audio      first binary speech frame (what the user hears first)
  tts_end / idle   speech finished / turn over

Usage (from AI_Agent/, server running with the voice extra):
  uv run python bench/voice_e2e.py --port 8765 [--turns 3] [--out DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import websockets

from sasha.config import load_settings
from sasha.pipeline.tts_kokoro import KokoroTTS

COMMANDS = [
    "Tell me one fun fact about cats.",
    "What is the capital of France?",
    "Give me a short tip for sleeping better.",
]
FRAME_BYTES = 16000 * 2 * 20 // 1000  # 20 ms of 16 kHz s16le


def to_16k(pcm: bytes, rate: int) -> bytes:
    import numpy as np

    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    n = int(len(x) * 16000 / rate)
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return bytes(y.astype("<i2").tobytes())


async def one_turn(url: str, pcm: bytes, timeout: float = 120.0) -> dict[str, Any]:
    marks: dict[str, float] = {}
    out: dict[str, Any] = {"audio_bytes": 0, "frames": [], "transcript": "", "reply": ""}
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "audio_start", "sample_rate": 16000}))
        for i in range(0, len(pcm), FRAME_BYTES):
            await ws.send(pcm[i : i + FRAME_BYTES])
            await asyncio.sleep(0.02)  # real time, like a microphone
        await ws.send(json.dumps({"type": "audio_stop"}))
        t0 = time.perf_counter()
        seen_thinking = False
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout)
            now = time.perf_counter() - t0
            if isinstance(msg, bytes):
                marks.setdefault("first_audio", now)
                out["audio_bytes"] += len(msg)
                continue
            frame = json.loads(msg)
            kind = frame.get("type")
            out["frames"].append(kind if kind != "state" else f"state:{frame['state']}")
            if kind == "state" and frame["state"] == "thinking":
                seen_thinking = True
            if kind == "transcript":
                marks.setdefault("transcript", now)
                out["transcript"] = frame["text"]
            elif kind == "reply_delta":
                marks.setdefault("first_text", now)
            elif kind == "reply":
                marks["reply"] = now
                out["reply"] = frame["text"]
                out["server_timing"] = frame.get("timing")
            elif kind == "tts_start":
                marks["tts_start"] = now
                out["tts_rate"] = frame["sample_rate"]
            elif kind == "tts_end":
                marks["tts_end"] = now
            elif kind == "error":
                out.setdefault("errors", []).append(frame["message"])
            elif kind == "state" and frame["state"] == "idle" and seen_thinking:
                marks["idle"] = now
                break
    out["ms"] = {k: round(v * 1000) for k, v in marks.items()}
    return out


async def run(args: argparse.Namespace) -> dict[str, Any]:
    base = f"http://127.0.0.1:{args.port}"
    health = httpx.get(f"{base}/api/health", timeout=10).json()
    settings = load_settings(profile=args.profile)
    tts = KokoroTTS(settings.profile.tts, settings.assistant.voice, settings.paths.root)
    clips = [to_16k(tts.synthesize(c, "en").pcm, 24000) for c in COMMANDS]
    url = f"ws://127.0.0.1:{args.port}/ws?speak=1"
    turns = []
    for i in range(args.turns):
        pcm = clips[i % len(clips)]
        result = await one_turn(url, pcm)
        result["command"] = COMMANDS[i % len(COMMANDS)]
        result["speech_s"] = round(len(pcm) / 32000, 2)
        turns.append(result)
        ms = result["ms"]
        print(f"turn {i + 1}: heard {result['transcript']!r}  {ms}")
    keys = ["transcript", "first_text", "first_audio", "tts_end", "idle"]
    warm = turns[1:] or turns
    summary = {
        k: round(statistics.median([t["ms"][k] for t in warm if k in t["ms"]]))
        for k in keys
        if any(k in t["ms"] for t in warm)
    }
    return {"health": health, "turns": turns, "median_ms_warm": summary}


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--profile", default=None)
    p.add_argument("--turns", type=int, default=3)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()
    report = asyncio.run(run(args))
    report["time"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    settings = load_settings(profile=args.profile)
    out_dir = args.out or settings.paths.data_dir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"voice_e2e-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("median (warm turns) ms from end of speech:", report["median_ms_warm"])
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
