# Research notes

Dated observations that could feed the paper. Keep numbers, setup and model versions.

## 2026-11-08

- Telemetry schema v1 is in place from Phase 0: per-stage latency, Ollama token
  counts and tokens/s, model digests, RAM/VRAM per request. Baseline for every
  later comparison (big vs small model, cascade vs direct, fast path vs LLM).
- Idle VRAM on the PC with nothing loaded: ~2.0–2.2 GB of 12.2 GB used (Windows
  desktop). Real headroom for models is about 10 GB, not 12.
