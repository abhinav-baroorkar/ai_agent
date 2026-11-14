# Decisions

Append-only log. Newest at the bottom.

## 2026-11-08 — Phase 0 setup

- **Package name `sasha` vs renamable persona.** The Python package and CLI are
  called `sasha` (project name). The persona name appears only in
  `config/assistant.yaml`; a test fails if it shows up in `src/`.
- **Env var prefix `SASHA_`** (`SASHA_PROFILE`, `SASHA_ASSISTANT_CONFIG`,
  `SASHA_ROOT`). Project-level, like the package name.
- **Ollama over raw HTTP (`httpx`) instead of the `ollama` Python client.** One
  fewer dependency, easy to mock in tests with `httpx.MockTransport`, and full
  access to timing fields (`eval_duration` etc.) for telemetry.
- **Model version = `name@digest[:12]`** from `/api/tags`, so logs pin exact weights.
- **Qwen3 `think: false`** by default: thinking tokens add seconds of latency for
  voice. Configurable per profile for experiments.
- **Last tool round withholds tools** so the model must answer in words; stops loops.
- **Chat history keeps only user/assistant text**, not tool messages, to keep
  small-model context short.
- **Home Assistant: three tools only, domain/service allowlist in
  `config/tools.yaml`, service-data key allowlist in code.** `entity_id: "all"`
  is expanded in code to exposed entities, so it can't bypass the allowlist.
- **Telemetry: JSONL per UTC day in `Data/logs/telemetry/`.** User text is not
  logged unless `telemetry.log_text: true` in the profile.
- **VRAM via NVML (`nvidia-ml-py`).** Works on the PC and in WSL2; returns `null`
  on Jetson (shared memory; use `jtop`/tegrastats there later).
- **5 s connect timeout** for Ollama and Home Assistant. In WSL2 an unused
  localhost port can hang instead of refusing, which stalled the CLI for 120 s.
- **No PyTorch in Phase 0.** It arrives with the voice stages (cu128 wheels on
  the PC, NVIDIA Jetson wheels on the Orin).
- **`pyproject.toml` lives in `AI_Agent/`** (all code there). Hatchling won't
  read a README outside the project dir, so the package has no `readme` field.

## 2026-11-08 — Repo publishing

- **One bootstrap commit straight to `main`** (README, LICENSE, .gitignore only),
  because an empty repo has no base branch for a PR. Everything after that goes
  through feature branches and PRs that the owner merges.
- **Commits use the GitHub noreply email**, so no personal address ends up in
  public history.

## 2026-11-14 — PR #1 review fixes

A separate local Claude session reviewed PR #1 (1 high, 3 medium, 8 low findings).
All of them are fixed in this branch.

- **Entity ids are checked against `^[a-z0-9_]+\.[a-z0-9_]+$` before any request**,
  and URL-quoted. Before this, `light.x/../lock.front_door` passed the domain check,
  and httpx's dot-segment handling made it read the lock (or `/api/config`).
- **Allowlist of controllable domains in code:** `light`, `switch`, `fan`, `scene`,
  `media_player`. Config loading fails if `tools.yaml` lists anything else. The
  first fix was a denylist, but the re-review showed it missed `button` (garage
  openers), `valve` (water shut-off), `notify`, `remote`, `update` and `siren`, and
  HA adds new domains every release. Owner voice confirmation can widen the list
  later (Phase 3).
- **Scenes:** one at a time (no `all`). Every member of a scene must pass the same
  exposure rule as direct control; otherwise a scene could reach an un-opted-in
  switch or a valve. **Switches are opt-in:** exposed only when listed in
  `exposed_entities`, because garage relays and door strikes are often switches.
- **Tool output is wrapped** as `{"source": ..., "untrusted_data": ...}`. Strings
  coming from HA are stripped of control characters and capped at 64 characters.
  HA error bodies are not passed to the LLM.
- **`HA_TOKEN` is a pydantic `SecretStr`**, so it never shows up in reprs or dumps.
- **`OLLAMA_HOST` is normalised** (`0.0.0.0`, bare `host:port`), and every httpx
  error or non-JSON reply becomes a clear `OllamaError`.
- **A failed request still reports which tools ran** (`RequestFailed.tool_calls`).
  Ctrl-C still writes a telemetry line but is re-raised unchanged (not wrapped), so
  a future `except Exception` in the voice loop can't swallow it.
- **`call_service.entity_id` stays `{"type": "string"}`** in the tool schema. The
  handler also accepts a list, but Ollama's template conversion may drop `anyOf`,
  and small models do better with simple schemas.
- **Telemetry `total_ms` is measured before the resource sample.** NVML is
  initialised once per process. It used to add 11–90 ms to every request's latency.
- **No-send test covers all of `src/`**, not only `gmail.py`.
- **System prompt uses `str.replace`**, so a literal `{` in the persona prompt
  doesn't crash startup. CI has `permissions: contents: read`.
- **Ollama models stay in Ollama's default location** (it runs as a system
  service). `env.sh` no longer sets `OLLAMA_MODELS`.

## 2026-11-14 — Workflow and model policy

- **No GitHub PR ceremony.** Solo project: feature branch → review by a separate
  local Claude session (nothing posted on GitHub) → fix → merge to `main` locally
  → push. Privacy scan before every push.
- **Don't downgrade models on the dev machines.** Use the strongest local models
  that fit (currently `qwen3.5:9b` on the 8 GB laptop GPU). Smaller models only for
  the Jetson profile.

## 2026-11-14 — Scope: one brain, many devices

- **One brain server, thin satellites.** The brain runs the LLM, speech, tools,
  memory and permissions. Satellites (laptops, PCs, a MacBook, the TV, the
  Jetson) do wake word, mic, speaker and face over home Wi-Fi. Why: the heavy
  models fit on one machine only, and a household has many rooms and devices.
  For now the brain is the laptop dev box and everything runs on it; satellites
  are Phase 5. The Jetson is also the future robot body and can later be a backup
  brain with smaller models.
- **Product pieces:** a main window with the animated face; a "bots" area with
  several named bots, each with its own model, instructions, connectors and chat;
  a settings screen with connectors. Why: one assistant is not enough once there
  are several jobs and several household members.
- **Connectors.** Home Assistant is built in. MCP servers are the plan for any
  other connector, so we don't write one adapter per service. Gmail and Calendar
  stay drafts-only and read-only. MCP output is untrusted data like all tool
  output.
- **Own WebSocket protocol:** JSON events plus raw 16 kHz 16-bit mono PCM frames.
  Why: browsers speak it natively, so the TV and phones can be web satellites,
  and it's simple to log. Home Assistant's Wyoming satellite project was archived
  in early 2026, so building on it would mean maintaining it ourselves.
- **Wake-word arbitration:** when several devices hear the wake word, the brain
  waits 300 to 500 ms and picks the one with the best wake score and loudness.
  The decision is logged. Why: the wait is short enough to feel instant, and the
  log becomes research data ("which device should answer").
- **Discovery by mDNS, manual URL fallback.** Why: mDNS is zero-config on most
  home networks but is blocked on some, so there must be a way to type the address.
- **Pairing with a 6-digit code;** each device gets its own token. Why: a lost
  or removed device can be revoked alone.
- **Echo handling on the satellite, half-duplex first.** The device doesn't
  listen while it speaks. Why: simplest thing that stops the assistant hearing
  itself; full-duplex echo cancellation can come later.
- **Alexa and Meta devices are optional, later, never core.** They are closed
  platforms where audio or text would pass through the vendor's cloud, which
  breaks the fully-local goal.
- **Model policy restated:** strongest local model that fits on dev machines
  (`qwen3.5:9b` on the laptop, measured 43 tokens/s, tool call in 1.6 s);
  smaller models only in the Jetson profile.
- **Phases renumbered** to put the brain server, window, bots and connectors in
  Phase 1, and satellites and the Jetson port in Phases 5 and 6. See `plan.md`.
