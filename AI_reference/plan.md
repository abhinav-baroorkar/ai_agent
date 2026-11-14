# Project plan

Living plan. Last updated 2026-11-14 (scope: one brain, many devices).

## Goal

A fully local assistant for the home that speaks **Marathi, English and
code-mixed Marathi-English**. You say the wake phrase, speak in either language,
and it controls the house through Home Assistant, helps with Gmail and Calendar
(reads and drafts only), and shows an animated face. The persona defaults to
"Sasha", but name, pronouns, wake word, voice and languages live in one config
file, so anyone can rename it.

It is also a research project from day one. Every request is logged with timings
and resource use, so the measurements can back a workshop paper. It is built in
public (GitHub, LinkedIn).

Prefer building our own parts (orchestrator, Marathi command layer, benchmark,
avatar) over gluing products together. Existing open models are building blocks
and baselines.

## Scope: one brain, many devices

- **One brain server** runs the LLM, speech models, tools, memory and
  permissions.
- **Small satellite apps** on each device (laptops, PCs, a MacBook, the TV, the
  Jetson) do wake word, mic, speaker and face. They talk to the brain over home
  Wi-Fi.
- **For now** the brain is the laptop dev box (native Ubuntu 24.04, RTX 5070
  Laptop 8 GB, 62 GB RAM) and everything runs on that one device. Satellites
  come in Phase 5.
- **The Jetson Orin Nano** is also the future robot body. Later it can act as a
  backup brain with smaller models.
- **Alexa and Meta devices** are closed platforms: audio or text would pass
  through the vendor's cloud. They are "optional, later" and never part of the
  core.

## Product pieces

1. **Main window.** The animated face you talk to, with subtitles.
2. **Bots.** Several named bots. Each has its own model, instructions and
   connectors, and its own chat.
3. **Settings with connectors.** A connector is how the assistant reaches a
   tool. Home Assistant is built in. The plan is to support Model Context
   Protocol (MCP) servers, so any MCP connector can be added. Gmail and Calendar
   stay drafts-only and read-only.

## Where it runs

| Machine | Role | Notes |
| --- | --- | --- |
| Laptop (native Ubuntu 24.04) | Brain and dev box now | RTX 5070 Laptop 8 GB, 62 GB RAM. Blackwell: PyTorch cu128+ only |
| PC (WSL2 Ubuntu 22.04) | Second dev box | RTX 5070 12 GB, 32 GB RAM |
| Jetson Orin Nano 8 GB | Satellite, robot body, backup brain | JetPack 6, ARM64, Python 3.10, "Super" mode |
| Other household devices | Satellites (Phase 5) | Laptops, a MacBook, the TV, phones, via native or web apps |

Python 3.10 via `uv` everywhere, so code runs unchanged on the Jetson. Every
model choice lives in a hardware profile (`config/profiles/`); porting is a
profile switch, not a rewrite.

**Model policy.** On dev machines use the strongest local model that fits:
`qwen3.5:9b` on the laptop (measured 43 tokens/s, tool call in 1.6 s). Smaller
models only in the Jetson profile. The Jetson-sized model is still run on the
dev machine for comparison; the big-vs-small gap is itself a result.

## Architecture: the voice loop

Each stage sits behind one small `typing.Protocol`, so models swap by config and
can be A/B tested. Every stage has a fake, so tests need no GPU.

| # | Stage | Dev choice | Notes |
| --- | --- | --- | --- |
| 0 | Mic + VAD | `sounddevice`, Silero VAD | Heavy models only run on speech |
| 1 | Wake word | openWakeWord, custom "Hey <name>" model | Train with negative phrases; check training-data licences |
| 2 | Speaker ID | SpeechBrain ECAPA-TDNN | Cosine ~0.6; convenience, not security |
| 3 | Language ID + STT | faster-whisper small (en), IndicConformer (mr) | |
| 4 | mr to en | IndicTrans2 distilled 200M | Skipped for English |
| 5 | Brain + tools | Ollama, `qwen3.5:9b` on the laptop | Few, simple tools |
| 6 | en to mr | IndicTrans2 distilled 200M | Only if the user spoke Marathi |
| 7 | TTS | Indic Parler-TTS (named Marathi voices) | Heaviest; fallbacks MMS-TTS-mar, Piper |
| - | Vision | Qwen3-VL, loaded on demand | Marathi text in images: also try OCR |

**Fast path.** A Marathi and code-mixed phrase table maps common home commands
straight to Home Assistant, skipping translation and the LLM. It tolerates STT
spelling variants and has aliases in both scripts. Every hit and miss is logged.

**Memory.** A 12 GB card holds roughly LLM 6 GB, TTS 2 GB, STT 1 GB,
translation 1 GB. The 8 GB laptop and the Jetson cannot hold everything: swap
models, move STT and translation to CPU, or use smaller ones. VRAM per stage is
logged so this shows up in data.

### Satellite protocol (Phase 5)

- Our own WebSocket protocol: JSON events plus raw 16 kHz 16-bit mono PCM
  frames. Browsers speak it natively, so the TV and phones can be web satellites.
- Discovery by mDNS, with a manual URL fallback.
- Pairing with a 6-digit code; each device gets its own token.
- Echo handling on the satellite, half-duplex first.
- When several devices hear the wake word, the brain waits 300 to 500 ms and
  picks the one with the best wake score and loudness. The decision is logged.

## Tools ("hands") and connectors

- **Home Assistant** is the hub for devices. The LLM gets only `list_devices`,
  `get_state`, `call_service`, limited to an allowlist of domains and services
  and to exposed entities. Locks, alarms and anything unlisted are refused.
- **Gmail:** `search_mail`, `read_mail`, `create_draft`. No send function exists
  in code, and a test enforces it.
- **Calendar:** read-only.
- **MCP connectors (Phase 4):** added in settings, per bot. Treated like any
  other tool: allowlisted, output untrusted.
- **LinkedIn:** drafting text only, no automation, no scraping.
- Fetched email, web and device text is untrusted data. Outside-world actions
  need a spoken "yes" from a recognised speaker.

**Permissions by speaker:** owner gets everything; other enrolled household
members only their own opted-in accounts; guests get lights only.

## Avatar

Browser page fed over WebSocket. States: idle, listening, thinking, speaking
(mouth from TTS loudness about every 30 ms) plus subtitles. Cards for weather,
calendar, pets, camera. Start 2D (own SVG or canvas). 3D (VRM, three.js) and a
printed pan-tilt head (servos, ROS 2 Humble) come later. Original character only.

## Roadmap and gates

Each phase ends with a demo and a gate. Don't start the next phase until the
gate passes.

| Phase | Build | Research and publishing | Gate | Status |
| --- | --- | --- | --- | --- |
| 0 | Repo, config, telemetry, Ollama LLM stage, Home Assistant tool, text CLI | Telemetry on; first devlog | LLM turns on a real light via Home Assistant from the text CLI | Done except the real-light gate, which waits for Docker |
| 1 | Brain server, main window with face, bots, connectors screen, English voice loop | Rename is config only; release v0.1 and demo clip | Lights work by voice in English | In progress |
| 2 | Marathi: IndicConformer, IndicTrans2, Indic Parler-TTS, fast path | Record code-mixed command set; cascade vs direct; v0.2 | A Marathi light command works end to end | |
| 3 | Speaker ID and permissions, pets, camera vision | Speaker ID by room, distance and noise | Guests can only control lights | |
| 4 | Gmail drafts, calendar brief, MCP connectors | Prompt-injection tests; demo video | Morning brief on screen; drafts only | |
| 5 | Satellites on other devices, wake-word arbitration | Which device should answer; Wi-Fi latency per satellite | Two devices hear one wake word, the right one answers | |
| 6 | Jetson port and backup brain | Big vs small model: quality, latency, watts; paper draft | All tests pass on the Jetson | |
| Later | 3D avatar, robot body | | | |

## Research

Telemetry: one JSON line per request in `Data/logs/telemetry/` with request id,
timestamp, hashed speaker, language, per-stage latency, model names and
versions, VRAM and RAM, tokens/s, fast-path hit, tool calls, success, error,
profile and machine. `bench/` scripts rerun every experiment from config;
tables (no personal data) go to `research-notes.md`.

Candidate studies (check existing papers before committing):

1. Marathi-English code-mixed home-command dataset and benchmark (consented
   recordings, released later).
2. Cascade (STT, MT, LLM, MT, TTS) vs direct Marathi into the LLM: task success,
   wake-to-first-audio, VRAM, watts.
3. Fast-path intent matcher vs LLM tool calling for a low-resource language.
4. Far-field speaker ID by room, distance and noise.
5. Big model vs small model on the same tests; can the big model generate
   Marathi data to fine-tune the small one?
6. Qwen3 thinking on vs off: latency vs tool-call accuracy.
7. **Which device should answer:** how well best-score-and-loudness arbitration
   picks the device the person meant, from the logged decisions.
8. **Wi-Fi latency per satellite:** added delay and jitter per device and room,
   and its effect on wake-to-first-audio.

## Open source

Apache-2.0. No model weights, secrets, recordings, emails or voice embeddings in
git. Renamable through `assistant.yaml`, a wake-word training script, language
packs and a setup script that picks models to fit the hardware. Tag a release per
phase; keep a devlog entry per session; add a LinkedIn note per phase.

## Privacy

Audio is processed only after the wake word and is not saved by default. A
"forget the last 5 minutes" command. LAN-only APIs (a VPN for remote use).
Tokens and voice embeddings stored encrypted. A hardware mute where possible.
Voice ID is convenience, not security.

## Workflow

Solo project, no GitHub PR ceremony. A team of local Claude sessions works in git
worktrees. A manager session assigns tasks, a reviewer session reviews each
branch locally, and the manager merges to `main` and pushes. A privacy scan runs
before every push.

## Open questions

- Pet device brands.
- Whether other household members want to be enrolled.
- Which Marathi voice to use.
- Spare screen connector and resolution.
- Jetson RAM headroom with the full voice stack.
