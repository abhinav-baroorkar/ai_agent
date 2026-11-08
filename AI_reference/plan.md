# Project plan (summary)

Summary of the full build plan (kept privately in `User_reference/`). Last
updated 2026-11-08.

## Goal

A fully local, bilingual (Marathi + English) voice assistant. You say the wake
phrase, speak in either language, and the assistant controls the house through
Home Assistant, helps with Gmail and Calendar (drafts and reads only), and shows
an animated face on a spare screen. Persona defaults to "Sasha" but everything
about the persona lives in one config file so anyone can rename it.

It's also a research project from day one. Every request is logged with timings
and resource use, so the measurements can later back a paper.

## Where it runs

1. **PC first** (WSL2 Ubuntu 22.04, RTX 5070 12 GB, 32 GB RAM). Larger models,
   faster iteration. Blackwell GPU means PyTorch cu128+ only.
2. **Jetson Orin Nano 8 GB later** (JetPack 6.2, Ubuntu 22.04, ARM64, "Super"
   mode). Same code; the port is a profile switch (`profiles/pc.yaml` →
   `profiles/jetson-orin-nano.yaml`), not a rewrite.

Also run the Jetson-sized 4B model on the PC from the start. The big-vs-small
gap is itself a result, and the big model can generate Marathi training data
for the small one.

## Architecture: the voice loop

Each stage sits behind one small interface so models can be swapped (and A/B
tested) by config.

| # | Stage | PC choice | Notes |
| --- | --- | --- | --- |
| 0 | Mic + VAD | `sounddevice`, Silero VAD | Heavy models only run on speech |
| 1 | Wake word | openWakeWord, custom "Hey <name>" model | 3 syllables; train with negative phrases |
| 2 | Speaker ID | SpeechBrain ECAPA-TDNN | Cosine similarity ~0.6; convenience, not security |
| 3 | Language ID + STT | faster-whisper small (en, lang ID), IndicConformer (mr) | |
| 4 | mr → en | IndicTrans2 distilled 200M | Skipped for English |
| 5 | Brain + tools | Ollama, Qwen3 8B 4-bit on PC; Qwen3/3.5 4B on Jetson | Few, simple tools |
| 6 | en → mr | IndicTrans2 distilled 200M | Only if the user spoke Marathi |
| 7 | TTS | Indic Parler-TTS (named Marathi voices) | Heaviest; fallbacks: MMS-TTS-mar, Piper |
| — | Vision | Qwen3-VL 8B swapped in on demand | |

**Fast path:** a Marathi/code-mixed phrase table maps common home commands
("सगळे दिवे बंद कर") straight to Home Assistant, skipping translation and the LLM.

**Memory budgets:** PC ≈ 6 GB LLM + 2 GB TTS + 1 GB STT + 1 GB translation +
2 GB headroom. Jetson ≈ 8.5–9 GB with everything loaded, slightly over 8 GB.
Options: Home Assistant on a Pi, a 2–3B LLM, or a lighter TTS. Measure with
`jtop`.

## Tools ("hands")

- **Home Assistant** is the single hub for all devices (HubSpace, WiZ, Govee,
  pet camera/fountain/litter box). The LLM gets only `list_devices`,
  `get_state`, `call_service`, limited to exposed devices.
- **Gmail:** `search_mail`, `read_mail`, `create_draft`. No send function
  exists in code at all.
- **Calendar:** read-only.
- **LinkedIn:** drafting text only (no API, no scraping).
- Fetched email/web text is untrusted data. Outside-world actions need a spoken
  "yes" from a recognised speaker.

**Permissions by speaker:** owner gets everything, other enrolled household
members only their own opted-in accounts, guests get lights only.

## Avatar

Full-screen Chromium kiosk page fed over WebSocket. States: idle, listening,
thinking, speaking (mouth driven by TTS loudness every ~30 ms; subtitles).
Info cards: weather, calendar, pet devices, camera. Start 2D (Rive or
SVG/canvas). 3D (VRM + three.js) and a printed pan-tilt body (PCA9685, ROS 2
Humble) come later.

## Open source and research

- Apache-2.0. No model weights, secrets, recordings, emails or voice embeddings
  in git. Check wake-word training data licenses (some are non-commercial).
- Renamable: `assistant.yaml`, a wake-word training script, language packs,
  plugin-style skills, and a setup script that picks models to fit the hardware.
- Public from Phase 0. Tag a release per phase and keep a devlog entry per
  session (these feed the LinkedIn posts).
- Research directions: (1) Marathi–English code-mixed command dataset and
  benchmark; (2) cascade (translate) vs direct Marathi into the LLM on the edge:
  success, latency, RAM, watts; (3) fast-path matcher vs LLM tool calling;
  (4) far-field speaker ID at home.
- Habits: JSON log per request, reproducible `bench/` scripts, versioned
  datasets, dated surprise notes.

## Roadmap (phases)

| Phase | Deliverable | Release |
| --- | --- | --- |
| 0 | Repo, config, telemetry, Ollama LLM stage, Home Assistant tool, text CLI | — |
| 1 | English voice loop (wake → STT → LLM → TTS) | v0.1 |
| 2 | Marathi (IndicConformer, IndicTrans2, Parler-TTS, fast path) | v0.2 |
| 3 | Speaker ID + permissions, Gmail/Calendar tools | |
| 4 | Avatar on the spare screen | |
| 5 | Jetson Orin Nano port + benchmarks | |

Each phase ends with a demo and a gate; don't start the next until the gate
passes.

## Privacy

Audio is processed only after the wake word and is never saved by default. A
"forget the last 5 minutes" command. LAN-only APIs (Tailscale for remote).
Tokens and voice embeddings stored encrypted. A hardware mute.

## Open questions

- Jetson RAM / JetPack version to confirm.
- Pet device brands.
- Whether other household members want to be enrolled.
- Which Marathi voice to use.
- Spare screen connector and resolution.
