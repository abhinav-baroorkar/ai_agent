"""Bench the real speech stages: load time, STT real-time factor, TTS time-to-first-audio,
and RAM/VRAM per stage.

No recordings are used: the test speech is made by the TTS stage itself (synthetic,
so no personal audio), resampled to 16 kHz and fed to STT.

Usage (from AI_Agent/, after `uv sync --extra voice [--extra voice-gpu]` and
`uv run python scripts/download_models.py --voice --no-llm`):
  uv run python bench/voice_stages.py [--profile pc] [--stt-devices cuda,cpu]
      [--with-llm MODEL] [--out DIR]

`--with-llm` first loads that (already installed) Ollama model onto the GPU, to see
whether STT still fits next to it. Results: one JSON file in `<Data>/results/`.
"""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from sasha.config import Settings, load_settings
from sasha.pipeline.stt_whisper import FasterWhisperSTT
from sasha.pipeline.tts_kokoro import KokoroTTS
from sasha.telemetry.resources import snapshot_resources

SENTENCES = [
    "Turn on the bedroom light.",
    "What is on my calendar tomorrow morning?",
    "Set the living room lights to thirty percent and switch off the fan.",
    "Remind me to refill the water fountain for the cat in two hours.",
]
REPLY = (
    "Sure, I turned on the bedroom light. It is now at full brightness. "
    "The living room lights are still off. Do you want me to dim them as well?"
)


def _mem() -> dict[str, float | None]:
    s = snapshot_resources()
    return {"rss_mb": s.process_rss_mb, "vram_mb": s.vram_used_mb}


def _delta(before: dict[str, float | None], after: dict[str, float | None]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in before:
        b, a = before[key], after[key]
        out[key] = None if a is None or b is None else round(a - b, 1)
    return out


def to_16k(pcm: bytes, rate: int) -> bytes:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    n = int(len(x) * 16000 / rate)
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return bytes(y.astype("<i2").tobytes())


def bench_tts(settings: Settings, runs: int) -> tuple[dict[str, Any], KokoroTTS]:
    before = _mem()
    t0 = time.perf_counter()
    tts = KokoroTTS(settings.profile.tts, settings.assistant.voice, settings.paths.root)
    load_s = time.perf_counter() - t0
    list(tts.stream("Warm up.", "en"))
    ttfa, rtf = [], []
    for _ in range(runs):
        start = time.perf_counter()
        first: float | None = None
        samples = 0
        for clip in tts.stream(REPLY, "en"):
            first = first if first is not None else time.perf_counter() - start
            samples += len(clip.pcm) // 2
        total = time.perf_counter() - start
        ttfa.append(first or total)
        rtf.append(total / (samples / 24000))
    return {
        "model": tts.model_version(),
        "load_s": round(load_s, 2),
        "ttfa_ms_median": round(statistics.median(ttfa) * 1000),
        "rtf_median": round(statistics.median(rtf), 3),
        "mem_delta": _delta(before, _mem()),
    }, tts


def bench_stt(
    settings: Settings, device: str, clips: list[tuple[str, bytes]], runs: int
) -> dict[str, Any]:
    cfg = settings.profile.stt.model_copy(update={"device": device})
    before = _mem()
    t0 = time.perf_counter()
    try:
        stt = FasterWhisperSTT(cfg, settings.paths.root, settings.assistant.languages.supported)
    except Exception as e:
        return {"device": device, "error": str(e)}
    load_s = time.perf_counter() - t0
    rtfs, latencies, results = [], [], []
    for text, pcm in clips:
        seconds = len(pcm) / 2 / 16000
        times = []
        for _ in range(runs):
            start = time.perf_counter()
            result = stt.transcribe(pcm, 16000)
            times.append(time.perf_counter() - start)
        latency = statistics.median(times)
        latencies.append(latency)
        rtfs.append(latency / seconds)
        results.append({"expected": text, "heard": result.text, "lang": result.language})
    out = {
        "device": device,
        "model": stt.model_version(),
        "load_s": round(load_s, 2),
        "latency_ms_median": round(statistics.median(latencies) * 1000),
        "rtf_median": round(statistics.median(rtfs), 3),
        "mem_delta": _delta(before, _mem()),
        "transcripts": results,
    }
    del stt
    gc.collect()
    return out


def load_llm(settings: Settings, model: str) -> dict[str, Any]:
    """Ask the local Ollama to load `model` and keep it (no pull, no new downloads)."""
    host = settings.secrets.ollama_host
    httpx.post(
        f"{host}/api/generate", json={"model": model, "keep_alive": "10m"}, timeout=180
    ).raise_for_status()
    ps = httpx.get(f"{host}/api/ps", timeout=10).json().get("models", [])
    info = next((m for m in ps if m.get("name", "").startswith(model)), {})
    return {
        "model": model,
        "size_mb": round(info.get("size", 0) / 2**20),
        "size_vram_mb": round(info.get("size_vram", 0) / 2**20),
    }


def table(report: dict[str, Any]) -> str:
    rows = ["| stage | device | load s | latency / TTFA ms | RTF | RAM +MB | VRAM +MB |"]
    rows.append("| --- | --- | --- | --- | --- | --- | --- |")
    t = report["tts"]
    rows.append(
        f"| TTS {t['model']} | cpu | {t['load_s']} | {t['ttfa_ms_median']} (first audio) | "
        f"{t['rtf_median']} | {t['mem_delta']['rss_mb']} | {t['mem_delta']['vram_mb']} |"
    )
    for s in report["stt"]:
        if "error" in s:
            rows.append(f"| STT | {s['device']} | error: {s['error'][:60]} | | | | |")
            continue
        rows.append(
            f"| STT {s['model']} | {s['device']} | {s['load_s']} | {s['latency_ms_median']} | "
            f"{s['rtf_median']} | {s['mem_delta']['rss_mb']} | {s['mem_delta']['vram_mb']} |"
        )
    return "\n".join(rows)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", default=None)
    p.add_argument("--stt-devices", default="cuda,cpu")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--with-llm", default=None, help="Installed Ollama model to load first")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--threads", type=int, default=None, help="Override STT and TTS CPU threads")
    args = p.parse_args()

    settings = load_settings(profile=args.profile)
    if args.threads is not None:
        settings.profile.stt.cpu_threads = args.threads
        settings.profile.tts.threads = args.threads
    report: dict[str, Any] = {
        "bench": "voice_stages",
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": settings.profile.name,
        "threads": args.threads,
        "start": snapshot_resources().to_dict(),
    }
    if args.with_llm:
        report["llm"] = load_llm(settings, args.with_llm)
    report["tts"], tts = bench_tts(settings, args.runs)
    clips = [(s, to_16k(tts.synthesize(s, "en").pcm, 24000)) for s in SENTENCES]
    report["stt_audio_s"] = [round(len(c) / 32000, 2) for _, c in clips]
    report["stt"] = [
        bench_stt(settings, d.strip(), clips, args.runs) for d in args.stt_devices.split(",")
    ]
    report["end"] = snapshot_resources().to_dict()

    out_dir = args.out or settings.paths.data_dir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"voice_stages-{settings.profile.name}-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(table(report))
    for s in report["stt"]:
        for r in s.get("transcripts", []):
            print(f"  [{s['device']}] {r['lang']}: {r['heard']}")
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
