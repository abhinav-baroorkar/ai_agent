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
  170 ms warm; 49 to 55 tokens/s; cancel returns to idle in 23 ms. (Measured by the
  author on the branch, not yet reviewed.)
- **VRAM budget on the 8 GB card:** LLM 5.2 GB at 8k context (fully on GPU) + STT 0.8 GB
  = 7.1 of 8.15 GB used. About 1 GB headroom. Risk: a bigger context could push LLM
  layers to the CPU.
- **Surprise:** with two supported languages, Whisper's language detection costs a second
  encoder pass (2.1 s vs 1.2 s on CPU; negligible on GPU).
- **Surprise:** on this hybrid CPU, 8 threads beat 16 and the default for Kokoro; the
  efficiency cores slow it down.
- Not yet measured: the full round trip (hear, think, speak) end to end.

