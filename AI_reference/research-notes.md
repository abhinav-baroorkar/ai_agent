# Research notes

Dated observations that could feed the paper. Keep numbers, setup and model versions.

## 2026-11-08

- Telemetry schema v1 is in place from Phase 0: per-stage latency, Ollama token
  counts and tokens/s, model digests, RAM/VRAM per request. Baseline for every
  later comparison (big vs small model, cascade vs direct, fast path vs LLM).
- Idle VRAM on the PC with nothing loaded: ~2.0–2.2 GB of 12.2 GB used (Windows
  desktop). Real headroom for models is about 10 GB, not 12.

## 2026-11-14

Laptop, RTX 5070 Laptop 8 GB, power profile "quiet" (so CPU numbers are pessimistic).
3 runs, medians, 8 CPU threads.

| Stage | Device | Load (s) | Latency (ms) | Real-time factor | RAM +MB | VRAM +MB |
| --- | --- | --- | --- | --- | --- | --- |
| TTS Kokoro-82M | CPU | 1.35 | 457 to first audio | 0.25 | 499 | 0 |
| STT Whisper small float16 | GPU | 1.34 | 127 | 0.049 | 1183 | 785 |
| STT Whisper small int8 | CPU | 0.56 | 1751 | 0.65 | 465 | 0 |

- **Brain server with `qwen3.5:9b`:** first streamed text 6.1 s cold (model load), 150 to
  170 ms warm; 49 to 55 tokens/s; cancel returns to idle in 23 ms.
- **VRAM budget on the 8 GB card:** LLM 5.2 GB at 8k context (fully on GPU) + STT 0.8 GB
  = 7.1 of 8.15 GB used. About 1 GB headroom. Risk: a bigger context could push LLM
  layers to the CPU.
- **Surprise:** with two supported languages, Whisper's language detection costs a second
  encoder pass (2.1 s vs 1.2 s on CPU; negligible on GPU).
- **Surprise:** on this hybrid CPU, 8 threads beat 16 and the default for Kokoro; the
  efficiency cores slow it down.
- Not yet measured: the full round trip (hear, think, speak) end to end.
- **Brain server, final:** short replies take 225 to 312 ms in total at 49 to 55 tokens/s
  (`qwen3.5:9b`, laptop, warm).

## 2026-11-14 (evening): full spoken turn, and load order

A full spoken turn works end to end on the laptop with synthetic speech streamed by a
script. Not yet measured with a real microphone in the window.

- Warm medians from the end of speech: transcript 126 ms, first reply text 254 ms, first
  audio 956 ms, done in about 1.5 s.
- Cold first turn after startup: first audio about 1.5 s (was 9.4 s before the warm-up).
- **Finding: load order decides GPU placement.** With speech-to-text loaded on the GPU
  first, the LLM spilled 17% onto the CPU and ran at 36 tokens/s. With the LLM loaded
  first everything fits (7.35 of 8.15 GB) and it runs at 58 tokens/s.
- A/B: starting the first piece of an answer early (first sentence as soon as it ends, or
  a cut at a pause after 40 characters) took first audio from 1085 to 956 ms.

