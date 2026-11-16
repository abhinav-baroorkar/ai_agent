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
  and it's simple to log. Home Assistant's Wyoming satellite project
  (https://github.com/rhasspy/wyoming-satellite) was archived on 2026-01-27; its
  README names OHF-Voice/linux-voice-assistant, which uses the ESPHome protocol,
  as the replacement. We don't use that either: it needs Home Assistant in the
  voice path and isn't browser-native, so the TV and phones couldn't be web
  satellites.
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

## 2026-11-14 — Phase 1 started before the Phase 0 gate

- **Phase 1 started early on the owner's instruction.** The owner asked for the
  window with face and voice to be built now. Why: the Phase 0 gate (a real light
  turned on through Home Assistant) waits on a Docker install only the owner can
  do, and nothing else is blocked by it.
- **The Phase 0 real-light gate stays open** and must pass before the v0.1
  release. Phase 0 is "done except the real-light gate", not plain "done".

## 2026-11-14 — Brain server design (`feat/brain-server`, not yet merged)

- **FastAPI + uvicorn**, one assistant per WebSocket connection. Why: a connection is
  one conversation, so history and cancel state can't leak between clients.
- **Bots and connector settings are stored as JSON under `Data/state/`.** Why: simple,
  local, git-ignored, and easy to inspect; a database can come later.
- **The window is Chrome in app mode with its own profile under `Data/chrome-profile`.**
  Why: a kiosk-like window with no extra dependency, and the stay-inside-the-folder
  rule keeps the profile in the project.
- **Voice plugs in through one function** that returns the speech-to-text and
  text-to-speech stages, or nothing. Why: text chat keeps working when voice is not
  installed, as the API contract requires.
- **Wrong `Host` header and cross-site WebSocket origin both get HTTP 403** (checked by
  the author). Why: a web page open in the browser must not be able to talk to the
  local brain.

## 2026-11-14 — Voice stages (`feat/voice-stages`, in review, not merged)

- **Speech-to-text: faster-whisper `small`, GPU when CUDA libraries are present,
  automatic fallback to CPU.** Why: the GPU is about 13 times faster (127 ms vs 1751 ms
  for a 2 to 4 s command).
- **Text-to-speech: Kokoro-82M through `kokoro-onnx` on CPU, 24 kHz, one audio clip per
  sentence.** Why: speech starts before the answer is complete (457 ms to first audio).
- **CPU threads: 8.** Why: faster than 16 or the default on this hybrid CPU, where the
  efficiency cores slow it down.
- **English voice only tonight.** A Marathi transcript is answered in English until the
  Marathi voice (Phase 2) exists.
- **GPL text-to-phoneme packages stay in the optional `voice` extra only,** imported
  lazily, nothing GPL vendored or committed. A GPL-free path (lexicon-based G2P plus our
  own small ONNX runner) exists at the cost of worse pronunciation for unknown words.
  The owner decides whether to keep the optional install or spend time on that path.
- **Kokoro weights currently come from a third-party re-upload pinned to a revision.**
  Moving to the official Apache-2.0 repository is being looked at before merge.
- **Model weights live inside the project, under `Dependencies/hf/`** (802 MB), per the
  stay-inside-the-folder rule.

## 2026-11-14 — Brain server, final design (`feat/brain-server`, in review)

- **Binds to this computer only and refuses any other host.** Why: single device for now.
- **Checks the `Host` header and the browser `Origin` on every request and WebSocket.**
  Why: a web page open in the browser must not be able to drive the assistant.
- **Cancel is silent, and a cancelled turn is not added to history.** Why: the user
  stopped it; a half answer would confuse the next turn.
- **A second message while busy gets an error, not a queue.** Why: simple and
  predictable; the UI cancels first.
- **The main bot gets all connectors; a new bot starts with none.** Why: least access by
  default.
- **A user-made bot always gets the safety note appended to its instructions.** Why: a
  custom prompt must not remove the safety rules.
- **Connector status "ok" means set up and not failing; only Test pings.** Why: status
  checks must not call out to services on every page load.
- **The secrets file is re-read on each connector check.** Why: a secret can be added
  without restarting the server.
- **Message text is never logged.** Why: privacy; telemetry keeps timings only.
- **`sasha window` starts Chrome in app mode with its own profile and its own home
  directory under `Data/chrome-profile`, and does not fall back to the default browser.**
  Why: the stay-inside-the-folder rule, and no mixing with the owner's browser.
- **New packages:** fastapi (MIT), uvicorn (BSD-3), websockets (BSD-3), which pull in
  starlette (BSD-3) and opentelemetry-api (Apache-2.0, API only, no network). All fit
  Apache-2.0.

## 2026-11-14 — Web UI design (`feat/web-ui`, mock version at `15a81d7`)

- **Original character:** soft rounded head, capsule eyes. No film-style HUD.
- **Colour means state:** amber is the assistant, teal listening, violet thinking. Every
  state also has a word, so colour is never the only signal.
- **Dark theme only.** System fonts, with Noto Sans Devanagari for Marathi.
- **All server text is inserted as plain text, and replies are not rendered as
  markdown.** Why: server and tool output is untrusted data, and this blocks script
  injection through the page.
- **No dependencies; works with no server through `?mock=1`.** Why: the UI can be built
  and tested before the server is ready.

## 2026-11-14 — API contract v0.1

- **`speak=0|1` on the WebSocket.** Home speaks every answer; bot chat tabs are silent.
- **One conversation per socket; `PUT` connector returns the Connector; `pronouns` is an
  object; `voice.tts_languages` added.** v0.2 will add adding and removing MCP
  connectors.

## 2026-11-14 — Brain server hardening (merged, `main` at `c6e8b1e`)

Review took two rounds and fixed 16 findings. 170 tests pass on `main`. A live run on the
laptop showed the window loading its page from the local server, with zero network
connections outside this computer and nothing written outside the project folder.

- **The window's Chrome gets no route out:** a dead local proxy plus resolver rules.
  Why: privacy flags alone still let a fresh profile contact Google four times.
- **The window's temp folder is inside the profile under `Data/`.** Why: the
  stay-inside-the-folder rule.
- **Settings can only narrow which devices are exposed, never widen.** Switches and the
  device list are widened only by editing `tools.yaml`. Why: a page or a bot must not be
  able to grant itself more control.
- **The main bot starts with Home Assistant only; every later connector is opt-in per
  bot.** Why: least access by default.
- **At most 4 WebSockets at once and 1 MB per message.** Why: bounds resource use from a
  misbehaving or hostile local page.
- **A bot's model must be an installed one.** Why: a bad choice fails when saved, not in
  the middle of a conversation.
- **The process environment wins over the secrets file, at startup and on reload.** Why:
  a value set on purpose for one run must not be silently overridden by the file.
- **Speech starts before a long answer ends** by cutting at a comma or space after 200
  characters. Why: first audio sooner on long answers.

## 2026-11-14 — Face design direction (`feat/web-ui`)

- **The owner rejected the first face and any human character.** No human faces.
- **New direction:** the owner asked for a friendly living ball of light with elemental
  brush-art flair. An orb of light developed into a character, with a brush-art flourish
  per state (water when listening, wind when thinking, flame when speaking), drawn from
  scratch in the woodblock-print tradition. Two variants are with the owner: without
  eyes, and with two eyes of light. It runs at 60 fps in a real window. Why: an original
  character that is friendly without looking like a person or like another product.
- **Supersedes** the 2026-11-14 entry "Web UI design" on the character (soft rounded
  head, capsule eyes). The colour, dark-theme, font and plain-text decisions there still
  stand.

## 2026-11-14 — Secrets handling

- **Sessions may work with this project's own secrets-file handling** (the owner's
  permission, 2026-11-14, this project only). Secret values are never printed, logged or
  committed. Why: connector setup needs it; the values themselves stay out of every
  transcript.

## 2026-11-14 — Corrections and supersessions (appended)

- **Supersedes the wording of the "Secrets handling" entry above.** Owner, 2026-11-14, this project only: the owner allowed sessions to access this project's secrets file for now, because it holds nothing sensitive yet. Team practice is narrower: code and tests may load the file at run time (`load_dotenv`, `reload_secrets`) and may add or check variable names. Sessions don't open, print, search, copy or paste the file or any secret value, because anything a session reads lands in its transcript; tool output, logs, telemetry, commits and reports never contain one. If a permission rule or safety hook blocks a read or a tool, stop and tell Primary; don't work around it. Only the owner widens or withdraws this.
- **Supersedes** the line "The main bot gets all connectors; a new bot starts with none"
  in "Brain server, final design". The hardening entry is current: the main bot starts with
  Home Assistant only, and every later connector is opt-in per bot.
- **Correction to the hardening entry, live-run check.** "Nothing written outside the project
  folder" meant: after a live run there were no new entries in `/tmp` (browser), `~/.config`,
  `~/.cache`, `~/.pki` or the desktop keyring folder, and zero TCP peers outside this
  computer.
- **Correction to the "4 WebSockets, 1 MB" reason:** it bounds resource use from a
  misbehaving local client. A hostile web page can't connect at all, because the Origin check
  stops it.
- **Kokoro weights, on the voice branch (not merged yet):** they now come from the official
  `onnx-community/Kokoro-82M-v1.0-ONNX` repository, pinned to a full commit hash, with one
  voice packed into a small derived file. This replaces the third-party re-upload noted in
  "Voice stages".
- **Face direction update.** The owner found the painted ribbons wrong and asked for
  particles. The flair is now flowing particles in the orb's own style: mist that breathes at
  idle, particles drawn in when listening, currents when thinking, rising embers when
  speaking. The default is without eyes; eyes stay as a switch. This supersedes the
  brush-art flourish and the two-variant choice in "Face design direction".

## 2026-11-14 — Voice wiring and server follow-ups (merged, `main` at `b53a118`)

- **The server warms the LLM before loading the voice stages, keeps it loaded for 24 h, and
  checks its GPU share once, warning if it is not fully on the GPU.** Why: load order
  decides placement. Speech-to-text first pushed 17% of the LLM onto the CPU (36 tokens/s);
  the LLM first fits (58 tokens/s). Known limit: a reload after the keep-alive expires can
  split it again.
- **The speaker starts the first piece of an answer early:** the first sentence as soon as
  it ends, or a cut at a pause after 40 characters. Why: A/B took first audio from 1085 to
  956 ms. This is in addition to the 200-character cut for long answers.
- **`fix/server-followups` (`99ae0ac`):** the window blocks WebRTC UDP, checks its dead-proxy
  port and works on the IPv6 loopback. Home Assistant errors shown to the model never
  contain its address. Why: no route out of the computer, and no internal addresses in
  model-visible text.
- **Rule: the microphone is never open without a visible sign.** Recorded after review found
  the mic stayed open after leaving Home (fix in progress on `feat/web-ui`).
- **Face direction: the owner approved the particle orb.**

- **Correction:** the heading "Voice wiring and server follow-ups" above says `main` at
  `b53a118`, which was true when it merged. `main` is `5aa4ee5` after `chore/env-tmpdir`.

## 2026-11-14 — Window review rules and follow-ups (merged, `main` at `f2bfae5`)

- **Microphone lifecycle:** the mic is never open without a visible sign. Leaving Home,
  hiding the window or closing the page closes it. A start in progress can be cancelled.
  Repeated Talk presses open one microphone. Why: the owner must always be able to see
  when the assistant can hear.
- **The page has a Content-Security-Policy:** same-origin only, WebSockets to loopback
  only. Why: even if something injected script into the page, it has nowhere to send data.
- **A guard test fails on HTML-injection sinks or outside URLs in the web folder.** Why:
  server and tool text is untrusted; the rule "insert as plain text" is enforced, not
  just agreed.
- **The tab cap comes from the server's socket limit** (`health.limits.max_sockets`). Why:
  the UI and server can't disagree about how many conversations are allowed.
- **One voice brain on the GPU at a time for the team.** Why: two would not both fit in
  8 GB, and load order matters (see "Voice wiring").
- **`fix/server-followups-2`:** a dead proxy only on closed privileged ports; a malformed
  Home Assistant address becomes a warning, not a crash; `/api/models` lists only
  chat-capable models, with a `tools` flag, bounded to about 3 s; the window waits up to
  90 s on a cold start and prints progress. Contract v0.1.1.
- **`fix/warmup-timeouts`:** a short connect timeout for the model warm-up; voice turns
  record the STT and TTS model versions in telemetry. Why: fail fast, and keep runs
  reproducible.
- **`chore/env-tmpdir-notes`:** `env.sh` refuses to run outside a bash checkout of the repo
  and removes temp entries older than 7 days. Why: the temp folder is inside the project and
  must not grow without limit.

- **Correction to the Content-Security-Policy entry above.** Why: injected script can't fetch
  or open WebSockets to anything off this computer. It doesn't stop navigation, and it can
  still use the local API, which is why text is always inserted as plain text and the guard
  test enforces it.
- **Correction to the `fix/server-followups-2` entry above:** the 3 s bound applies to the
  capability checks behind `/api/models`, not to the whole endpoint.
- **Correction:** "`main` at `f2bfae5`" in the heading above was true when it merged; `main`
  is `d45935b` after `fix/stt-runtime-fallback` (`3f76eee`).

## 2026-11-14 — Always-on wake word (design stage, no code yet)

- **The owner asked for an always-on wake word on this device:** a listener that starts at
  login, hears the wake phrase, answers, and shows a small icon on screen. The server author
  writes a design note first, and the reviewer checks the design before any code.
- **No listening without a visible sign and a one-click pause.** Why: the same rule as the
  window's microphone; the owner must always be able to see and stop it.
- **Audio that doesn't begin with the wake phrase is dropped and never stored or logged.**
  Why: privacy; the assistant must not keep what it was not addressed with.
- **First version detects the phrase with the existing speech-to-text.** A trained wake-word
  model can replace it later. Why: nothing new to install or train to get a working loop.
- **The start-at-login file lives outside the project folder,** so the owner approves it or
  runs the install command personally. Why: the stay-inside-the-folder rule.

## 2026-11-14 — Wake word listener: approved design (three branches in progress, none merged)

Summary of the design that passed review (two rounds). Opt-in until the measurement plan passes.

- **Shape:** `sasha listen` is a thin always-on listener process, the first satellite. It
  sends gated speech to the brain's existing WebSocket and plays the reply itself. It never
  starts a brain (one voice brain on the GPU). `sasha autostart` sets up start at login.
  Branches: `feat/wake-server` (brain side), `feat/wake-listener` (the program) and
  `feat/wake-indicator` (tray icon and two short sounds).
- **Detection v1 is by transcription:** the brain's existing speech-to-text transcribes each
  gated segment, and a gate accepts only if the first word is the name (or a configured
  variant). The name comes from config, never hard-coded. "Name, command" runs the command in
  the same pass; the name alone arms a 5 s window for the next segment. Why: nothing new to
  train or install to get a working loop on this laptop. A trained on-device wake-word model
  can replace it later, and is the only design for other devices over Wi-Fi.
- **Rejected:** the name mid-sentence or at the end, possessives and continuations, near names
  (exact word match only), and the name alone with low speech-to-text confidence. A 3 s
  cool-down follows any turn.
- **Visible sign, fail closed.** The tray icon lives in the listener's process, and the
  microphone opens only while the icon is confirmed shown. If the indicator, the screen-lock
  state or the session state can't be read, the listener refuses to capture. The checks are
  proved at start and polled about every 2 s, not just subscribed to. Why: a subscription to a
  wrong signal name succeeds silently.
- **Pause closes the microphone for real** (the capture process is killed). It is also paused
  on screen lock, on suspend, on battery (default on) and while anything is speaking, plus a
  short margin. Why: half-duplex stops the assistant hearing itself, including across sockets.
- **Non-wake audio and its transcript are dropped:** never logged, stored, sent to the LLM or
  returned to the client. Dropping is structural: the wake handler has no reference to the
  assistant or to the telemetry text API. The listener writes no files. A debug audio dump
  exists only behind a CLI flag for that run, with a distinct tray state.
- **A short sound on every wake.** Why: a fullscreen app hides the icon.
- **Abuse limits on the wake path, enforced on the server whatever the bot settings say:**
  read-only by default. After the owner switches on device control for voice once in the Bots
  screen (never by a tool or a model), a single light, fan or media player acts directly.
  "All", any scene, or more than 3 distinct devices in one request ask for a spoken yes.
  Switches are never controlled by voice in this version. Volume is capped at 0.6. At most
  six device calls per minute. Why: no speaker check yet, so a stranger or a television gets
  no more than the lowest-risk actions.
- **Start at login through systemd user units generated inside the project** (`Data/
  autostart/`). `sasha autostart` shows both files and the exact link paths first, and
  reports the undo command. The brain unit starts with the graphical session, so an SSH login
  doesn't load the model onto the GPU. The listener unit doesn't require the brain, so a brain
  restart doesn't take the listener down; it goes offline and reconnects.
- **One reserved socket for the listener,** so the window's tab cap is unchanged.
- **Stays opt-in until the measurement plan passes:** false accepts over 8 h of mixed audio
  (at most 1, plus 0 on name-in-sentence audio), accept rate at 1, 3 and 5 m (at least 90%),
  microphone-closed checks, echo (0 wakes from the window's own speech), cost and power, and
  latency (within 0.6 s of the spoken-turn median).
- **New dependency approved by the reviewer:** jeepney (MIT) for the tray icon. Its licence
  row is added when the branch merges.
- **Limits of v1, stated plainly:** anyone in earshot or a television can trigger the same
  device commands as typed chat, within the limits above. A false wake on the name alone makes
  the next sentence a command. In a fullscreen app only the sound shows a wake.

## 2026-11-14 — Owner decisions on wake word and voice control

- **Wake phrase: the name alone,** read from config. The owner confirmed it is workable.
- **What voice may control:** read-only by default; after the owner switches on device control
  for voice once in the Bots screen, the limits above apply. Why: the owner chose the first
  option offered.
- **Start at login:** the owner allowed a session to create the start-at-login link. See the
  team rule "One allowed write outside the project".

## 2026-11-14 — Wake word corrections (appended)

- **Correction to the voice-control rule above:** the spoken yes applies to "all", any scene,
  or more than 3 distinct devices **in one turn (counted across all its calls)**, not "in one
  request". Why: a model can split one request into several one-device calls. Volume is capped
  at 0.6 for voice requests.
- **Addition to "One reserved socket for the listener":** the slot is identified only by the
  `wake=1` flag (local trust). It is not an authentication check; any local client could claim
  it. Per-device tokens will tie it to a listener later.
- **Contract numbering:** contract v0.2 (wake word) is written, and the server and listener
  sessions build against it. The MCP connector endpoints (add and remove), which an earlier
  entry planned for v0.2, are now planned for v0.3.

## 2026-11-14 — Owner decisions, night

- **Next feature after the wake word: the assistant's own browser.** Own profile inside the
  project, live view, take over, approval before anything that changes something, no access to
  the local network, and voice-started requests are read-only there. Ideas from an earlier
  project of the owner's only; own code.
- **The listener does not listen while the screen is locked.** (Matches the design.)
- **Secrets file:** sessions may access it for now; the owner will say when that stops. Team
  practice stays as written in the earlier secrets entries (load at run time, names only, don't
  open it unless a task needs it and Primary agrees).
- **The GPL text-to-phoneme question stays open.** Why: features first.
- **The real-light test (Docker and Home Assistant) is postponed for a few days.**
- **Claude's own memory folder for this project may be used for notes.**
- **`CLAUDE.md` edited on the owner's instruction:** step 3 (the manager merges after the
  reviewer approves) and the current phase (Phase 1, with the Phase 0 gate open and postponed).
  This resolves the earlier note that the owner would edit those lines.
- **Send guard stricter (`feat/wake-indicator`):** exemptions are pinned to exact lines, and
  mail-sending calls are forbidden in every file. Why: the rule is that no send function
  exists anywhere; the test must not have loose exceptions.

- **Correction to the secrets bullet in "Owner decisions, night" above.** It said sessions
  "don't open it unless a task needs it and Primary agrees". The rule has no such exception. The
  wording is: (code and tests load it at run time; sessions never open, print, search, copy or paste it or any value; a block means stop and tell Primary). The decision stands: the owner allows access for now and will say
  when that stops.
- **The phase-gate checkpoint** ("stop and show the owner at each phase gate") is kept in
  `CLAUDE.md`. It was added in `59e21a7` after the reviewer's note on `4e71b41`. Both `CLAUDE.md`
  commits were made on `main` at the owner's instruction and reviewed afterwards.

- **Send-guard exception: parked by the owner (2026-11-14) until Gmail is linked.** It stays as
  reviewed. The question "is the narrow exception acceptable, or must the rule be absolute?" is
  asked again before any Gmail code is merged (Phase 4).

## 2026-11-14 — Build order (owner, night)

- **First week: software only.** Device and robot work comes after that. Why: device work needs
  hands-on testing, scheduled after the first week.
- **No "fun" features.** Games, trivia, stories and a music visualiser are out.
- **Order of work:** Marathi–English first, then timers, alarms and reminders, memory, a morning
  brief, media control, local file questions, scheduled routines, "what's on my screen", computer
  helpers, web search through the assistant's own browser, calculation tools, speaker ID, lists,
  proactive reminders, MCP connectors with approvals plus bot templates and multi-step tasks, and
  the activity log with "forget the last 5 minutes" and a visible local status. The full list is in
  `plan.md`. The browser stays in flight next to Marathi. The morning brief's mail part waits for
  the parked send-guard question. Phase gates stay.
- **Rule: no real-microphone run without the owner present.** Why: the microphone is opened only
  with the owner at the machine; it is the most sensitive part of the project. The wake word has
  not run on a real microphone; real-microphone checks are scheduled with the owner.
- **Marathi–English research is facts-first** (models, licences, gated models, sizes, CPU
  feasibility); design follows. The owner will probably need to accept terms on some model pages
  and put a Hugging Face token into the secrets file personally.

## 2026-11-14 — Marathi–English is postponed, with a condition (owner)

- **Marathi–English support is skipped for now.** The owner's condition for ever adding it: the
  base assistant must keep working properly. **Rule:** Marathi support is optional and may only be
  added if it doesn't slow the English path (latency and GPU placement measured before and after);
  otherwise it stays out. Why: the English voice loop fits the 8 GB GPU with about 1 GB to spare,
  and load order already decided placement once (see "Voice wiring").
- **The first new feature is now timers, alarms and reminders,** then the rest of the build order
  in `plan.md`, renumbered. The assistant's own browser stays in flight.
- **Marathi research is saved locally** (facts first; summary in `research-notes.md`). It is not
  needed while Marathi is postponed.


## 2026-11-14 — Truthful replies on wake turns (merged, `main` at `61aa277`, `d5c9c43`)

- **On a wake turn the server holds the model's text for a change request** and then states what
  was and wasn't changed, from the tool results. Text is released sentence by sentence only while no
  device call has been attempted and no change is claimed. Why: a spoken "done" that is not true is
  worse than a slow answer; the model must not be the one that reports success.
- **Device control by voice can't be switched on without this.** Why: the opt-in in the Bots screen
  is only safe if the spoken sentence is always the factual one.
- **A read-only clock tool for every bot.** Why: timers and reminders need the current time, and the
  model should not guess it.
- **A bench of 42 request phrasings is a gate** (more phrasings of a change request are
  recognised). Why: a missed phrasing means an unchecked claim; the gate stops a quiet drop.

## 2026-11-14 — The microphone sign must agree with the capture process (merged, `bda9732`, `4968ef5`)

- **Finding:** a test that failed now and then was a real bug. While the capture process was
  starting, another part of the listener loop could redraw the tray icon as "microphone closed", so
  the listener could record for up to 2 s under a closed icon. Review found a matching case on
  close. No real microphone had been opened.
- **Decision:** the sign counts a capture as open from the moment it starts until it is killed and
  reaped. A test oracle tracks the real child process, so the sign and the process cannot disagree.
- **Rule (also in `team.md`):** an intermittent test failure is investigated as a possible product
  bug before it is called flaky. Primary's pre-push check shows the names of failing tests and stops
  on any failure. Why: one merge was pushed on a run with one failure before this was fixed.

## 2026-11-14 — Timers, alarms and reminders (first feature of the build order; Home card merged, server in final review)

- **Tools take structured fields; the server resolves the time and echoes it back.** It asks when
  something is unclear and never guesses. Why: a wrong time that is announced as right is the worst
  failure of a timer.
- **The server checks the model's fields against the person's own words.** A period (am or pm), a
  clock time or a delay that the person didn't say is dropped or asked about. Why: before this check
  the model invented am/pm or a delay in 7 of 144 runs.
- **Bench:** 62 phrasings through the real local model, 3 runs each; the bar is "0 silent wrong
  times" (met). Final numbers go in `research-notes.md` when the server branch reports.
- **"Wake me at 7" asks morning or evening** (owner's decision).
- **Late or missed items are announced with a generic line;** their text is read aloud only on
  request. Why: the text may be private and the room may not be.
- **Items created by voice are marked** as such.
- **A bare "stop" works without the name only while something rings,** within tight limits. "<name>,
  stop" stops whatever is ringing without a confirmation. Why: a ringing alarm must be easy to
  silence, and nothing else should be stopped by a bare word.
- **The Home timers card never lets Escape dismiss an alarm** (contract v0.3). Why: a key press
  must not silence an alarm by accident.

## 2026-11-14 — The assistant's own browser, phase 1 (merged; `main` at `39af3f3`)

- **Chrome runs over a DevTools pipe, never a TCP debug port.** Own marked profile under `Data/`,
  headless, muted. Why: no network door to the browser, and nothing outside the project folder.
- **All traffic goes through our own filtering proxy** that refuses loopback, private, link-local,
  metadata and mapped addresses. Measured on the real Chrome: every tested form refused, also from
  page sub-resources. Why: a web page must not reach the local network or the machine's services.
- **An address may be opened only if the owner wrote it in this conversation;** the server rebuilds
  it. Search is a separate tool. Why: text from a page or an email must not choose where to go.
- **A leak check on search queries** (emails, long numbers, identifiers from earlier messages). Why:
  an early run with the real model leaked an email through a search, so the guards do not rely on
  the model.
- **A browsing bot can have no other connector and no timer tools; no browser on wake turns.**
  Why: untrusted page text must not sit next to tools that change things, and a spoken request is
  read-only.
- **History files are wiped whenever the browser stops,** only inside a profile the assistant
  created (marker file), never through a link, never under a live Chrome.
- **Live view in the Bots screen:** frames as JPEG at most about 5 a second, a site line that comes
  from the brain (a page cannot write it), Stop always available. The HTTP frame route was removed
  (contract v0.4.1); frames travel only on the live-view socket.
- **Search engine: DuckDuckGo** (owner's decision). It served the headless browser a bot challenge.
  Rule: nobody works around it. The owner chooses between a search service with the owner's own key
  and solving it by take-over (not built).
- **Later phases:** acting with approvals, sign-in by take-over (low-value sites only), media.

## 2026-11-15 — Take-over comes first among the later browser phases (owner)

- **Supersedes** the open choice in "The assistant's own browser, phase 1": the search engine stays
  DuckDuckGo, and its bot challenge is solved by the owner personally, through a take-over of the
  assistant's browser. It is never automated and never worked around. The other option (a search
  service with the owner's own key) is not taken.
- **Take-over moves ahead of the other later browser phases** (acting with approvals, sign-in,
  media). The design, as agreed so far: a visible window, the brain disconnected while the owner
  drives, hand-back by restart. It is being designed now and gets a design review before any code,
  like the wake word and the browser core.
- **Why:** the owner can solve the check where a program must not try; keeping the brain
  disconnected while the owner drives means nothing the assistant could read or do is exposed
  during a take-over.
- **Timers bench (correction to the placeholder above):** on the server branch at its last report,
  186 runs: 117 right, 54 asked correctly, 15 asked needlessly, 0 wrong. One more change is being
  made (an answer is also read together with the previous message when the assistant's own reply
  was a question) and the bench will be re-run. Numbers in `research-notes.md`.

## 2026-11-15 — Corrections after the documentation review (appended)

- **Correction to "Timers, alarms and reminders" (bench).** "Met" says more than was verified. The
  bar "0 silent wrong times" was met on the author's own bench (62 phrasings, 3 runs each, the
  author's numbers). The reviewer's last review of the server branch found silent wrong times
  outside the bench (a number inside the reminder text read as the time; a morning cue with pm set
  for the evening; answers to the tool's own question failing). These are reported fixed, but the
  branch is not re-reviewed or merged. So: met on the author's bench; final review open; the server
  branch is not merged.
- **Correction to "The microphone sign must agree with the capture process".** The test oracle that
  tracks the real child process is a test, not a run-time guarantee. Read the sentence as: any
  disagreement between the sign and the process fails the tests.
- **Addition to "The assistant's own browser, phase 1" (live view and limits).** The frame cap
  ("at most about 5 a second") and "one bot at a time" each have a follow-up still open: two follow-ups are in progress on `fix/browser-turn-holder`: a page that reloads itself can exceed the frame cap, and two bots can share the browser within one turn.
  Both are fixed only when that branch is reviewed and merged.
- **Rule for these documents:** a number or a "met" is attributed (whose run, at which commit) and
  marked pending until the branch it belongs to is reviewed and merged.

## 2026-11-15 — Owner decisions and design choices since the browser phase (appended)

- **The wake phrase is "hey" plus the assistant's name, read from config.** This **supersedes** the
  2026-11-14 decision that the wake phrase is the name alone. Why: on the first real-microphone
  try, nothing happened; the name alone gave the speech-to-text no context and it guessed. With the
  speech-to-text hint (the wake phrase given as hotwords), 102 of 108 name-alone synthetic samples
  were accepted, up from 65, with no false wake in 116 ordinary sentences and mentions (the author's
  synthetic run, section 12 of the wake-word design note kept in the team folder). The false-wake
  sets passed with 0 hits in sets 1 and 3 (216 samples, the author's run). No hint is given on the
  armed follow-up (`fix/wake-hint-armed`). Where this stands: device control by voice stays off
  until a few days of the owner's real-room telemetry. "Sacha" and "Shasha" wake the assistant
  (36 of 36 synthetic samples; no confidence bar can tell them apart); the owner has checked this
  known limit.
- **Start-at-login is turned on** at the owner's request, under the rule in `team.md` (symlinks
  to unit files inside the project only; the undo command is reported).
- **The window becomes the assistant's own app:** Qt WebEngine through PySide6, in an optional `app`
  extra (PySide6 is LGPL). The Chrome window stays as the fallback (`sasha window --chrome`) until
  the owner's visible check passes. The wake phrase opens or raises the window with a calm orb
  entrance. Limits on the opener: once per 30 s, at most 6 an hour, never when the screen is
  locked. Network layers (hardening branch): an interceptor that allows only the own loopback origin, a
  content security policy with `connect-src 'self'`, and a bypass of loopback through the dead proxy.
  The author's tests show each layer holding alone (probes with the interceptor off and with only
  the content security policy; the headless-Chrome case is the author's run).
- **Bots area: Concept A** (owner's choice): a "Your bots" page with cards and one labelled entry
  on Home, with a microphone in bot chats. The voice bot is not shown as a second main assistant.
- **Per-bot breathing styles:** eight original particle styles (glow, ember, tide, gale, storm, mist,
  bloom, stone), the main orb included; glow is the default. The style is a field on the bot
  (contract v0.4.4), and a style name can only pick a real style.
- **The persona line ties offered abilities to the tools present** (the owner approved the wording).
  Why: an assistant must not offer what it cannot do; unknown tool names are left out.
- **Memory design is approved with conditions; not built yet.** Notes are made only when explicitly
  asked; notes made by voice stay pending until the owner keeps them on screen; wake turns read
  only notes marked "ok to say aloud"; every word and number in a note comes from the owner's own
  messages (checked word for word); no silent eviction at 200 notes (at the cap, new notes are
  refused and the reply says so). Why: memory is a lasting prompt channel, so a write that is
  only mostly checked becomes a lasting injection, and a read on a wake turn can leak. Also: a
  device change needs the owner's current message to name the device and the action, so a stored
  note can never cause one (this is what makes a stored note harmless); and the memory section is
  shown to the model as data and never passed to the browse helper.
- **Manager's defaults:** "next <weekday>" within 2 days asks back; after the owner's own check of
  sound-alike names, no extra confirmation is added to the wake phrase.
- **Rule: a false claim of an action is never spoken.** Status: in place for wake turns; for
  timers the rule is in the server code that merged on 2026-11-16 (see the timers section below).
  The server says its own "Nothing was set." or its own fixed question instead of the model's claim.
  Why: the server, not the model, says what really happened.
- **Spoken text is plain text:** no markdown is read aloud; a link or an http or www address is never read (a
  bare URL is said as its host; an address without a scheme, such as "example.com/path", is still
  read as written); a hold on the model's text never lasts beyond 200 characters; "2*3" is spoken
  as "2 times 3".
- **The clock tool is called only when asked;** a stopped turn stays in history with only what was
  released; Talk ends by itself when the owner stops speaking.
- **Private sound-alike names** for the wake bench are read only from the owner's own file or
  terminal and are never printed or saved in clear.

## 2026-11-16 — Timers, alarms and reminders merged (`feat/timers-server`, `fix/timer-days`; `main` at `fe710d8`)

- **Reviewed:** the re-review of `feat/timers-server` at `d37cc0a` found the earlier blocking item
  fixed (a false claim of "set" with a trailing question), and `fix/timer-days` at `5fb66d3` passed
  with nits only. Both merged at 00:18 local time on 2026-11-16. On `main` `fe710d8`: 1213 tests
  passed, 5 skipped, 2 deselected (the real-Chrome file).
- **A false "set" claim is never spoken.** On a request for a timer, if nothing was made and no tool
  asked back, the reply may stand only if every sentence is a question; otherwise the server adds or
  speaks its own line ("Nothing was set." or "Nothing is set yet."). Why: the model's wording cannot
  be trusted to match what happened ("Done. Anything else?" with nothing set). This closes the
  status noted under the 2026-11-15 rule "a false claim of an action is never spoken".
- **The server's own fixed questions.** When a tool asks back, the server speaks its own fixed
  question for the reason, not the model's words; the reason is a label, never free text, and the
  weekday in a question is accepted only if it is one of the seven weekday names. Why: a page or a
  reminder's text must not be able to put words in the assistant's mouth.
- **Status questions come from the real state.** "Is my alarm set?", "how long is left on my
  timer?" and "do I have any reminders?" are answered by the server from its own record, even if the
  model did not call the list tool; the line is built from kind and time only, never reminder text,
  so it is safe to say aloud. Mentions of a timer that are not requests ("what's a good timer for
  tea?", "thanks for the reminder") keep the model's reply. Why: a status answer must be true, and
  honest answers must not be replaced.
- **"Next <weekday>" within 2 days asks back** ("This Friday or the one after?"); otherwise it
  resolves. This is the manager's default, not an owner decision.
- **Status of the bench numbers:** the earlier bench numbers in `research-notes.md` are the author's
  (at `b253796`). The final run is Primary's: `qwen3.5:9b`, 90 cases x 2 at `89a2ab1`, re-scored
  with the rules of `d37cc0a` (0 silent wrong times, 0 spoken false claims, right or asked 0.95;
  offline probes 17 of 17); see the entry "2026-11-16: timers bench, final" in `research-notes.md`.
  The reviewer re-probed the server logic of `d37cc0a` offline; that was not a model run.

## 2026-11-16 — Correction to the timers entry above (appended)

- **Wording correction.** "When a tool asks back, the server speaks its own fixed question for the
  reason, not the model's words" holds when nothing was made. Read it as: when a tool asks back and
  nothing was made, the server asks its own fixed question for the reason; only a reply that is
  entirely questions may stand instead. Two cases differ from the short wording: on a typed turn, a
  reply that is all questions is kept as the model wrote it; on a spoken turn where something was
  also made, an all-questions reply is spoken as it is.

## 2026-11-16 — Browsing for the main assistant: the `browse` helper (merged, `feat/browse-helper` `a507613`)

Reviewed at `a507613` (LGTM; two should-fixes left for a follow-up, below). Decisions, with reasons:
- **B4: a bot never holds raw browser tools together with any connector that reads private data or
  changes things.** A bot with the direct browser has the browser only. Any other bot browses only
  through the `browse` helper, whose context has the browser tools and nothing else. Why: a web page
  can contain hidden instructions; if the same model context could read private data or change
  devices, the page could make it do so. Keeping the browser in its own context means a page can
  only affect a lookup.
- **Web lookups go to the main assistant only,** whenever the browser connector is on in Settings;
  there is no per-bot switch yet. Why: one place to get right first; per-bot lookups can come later.
- **Wake-word turns get no browsing.** Why: the room can't see the screen, so a voice turn can't
  show what was read, and spoken input is the easiest to spoof (a television, visitors).
- **P1 taint rule.** Every tool counts as changing unless marked read-only. In the same turn as a
  successful lookup, no changing tool runs. While a looked-up turn is still in context, a device
  change runs only if the owner's current message names the device and the action; anything else,
  including a bare "yes", is refused (it fails closed; there is no confirm wording in the window yet,
  so a "yes" after the assistant's question is refused while tainted). Why: after a page is read, the
  model's next question might be one the page planted ("Shall I unlock the door?"). "Yes" doesn't say
  which device or action, so only the owner naming both counts.
- **Spoken lookup answers are capped at 500 characters and end with "From <site>."** from the
  server's own record; chat tabs show a "looked up" line instead. Why: spoken answers can't be
  skimmed, and the source must always be heard, taken from the server's own record so a page can't
  fake it.
- **The helper's searches may use only the owner's current words, the task, and words from pages it
  has read.** Why: a page must not be able to make the helper search for things the owner never asked
  about.
- **While a web result is in the context, a tool that carries the owner's words** (for example a
  reminder's text) **runs only if every word of those fields is the owner's own,** from this turn or
  the previous one; glue words and plurals are allowed; cancel and snooze fail closed. Why: a page
  must not be able to put words into a reminder that is spoken at ring time.
- **Relaxation of the memory action rule (supersedes, for the target only, the rule in the 2026-11-15
  memory entry).** The device (never the action) may now be named in the owner's last 2 messages
  within 3 minutes, but only when the current message names no device of its own; locks, alarm
  panels and covers never carry a device over. "Turn it off" after "is the bedroom light on?" works;
  "turn off the TV" after it can't reach the bedroom light. Reviewed: a different device named now
  than earlier, or a negation ("don't turn it off"), is still refused. The action always comes from
  the current message. When it takes effect: the earlier messages are used only on bots with memory
  (`always_guard`), which arrive with `feat/memory-server`; until that merges, `main` behaves as
  before. This entry changes only where the target may be named.
- **Known open items, from the review (a follow-up is expected):** an excluded target can still
  count ("turn the kitchen light off, not the bedroom" passes the bedroom); the owner-words check
  drops a negation ("remind me not to call the bank" can pass "call the bank").
- The model's wording of a lookup reply is not trusted for the source or the length: both come from
  the server (P8).

## 2026-11-16 — Take-over of the assistant's browser (merged, `feat/browser-takeover` `9db9b10`; contract v0.5)

- **The owner can drive the assistant's browser in a visible window for up to 15 minutes.** The
  brain is disconnected while the owner drives (no automation connection, no screenshots), and hand-back
  is by restarting the assistant's own headless browser, which starts with only the search engine's
  cookies on its next use. Why:
  the owner sometimes has to solve a person-check; 15 minutes bounds it; a forgotten window must not
  keep the bots out of the browser.
- **On hand-back, downloads, form data, logins and history are wiped and only the search engine's
  cookies are kept.** Why: the wipe means nothing the owner typed stays for a bot to see; the search
  engine's cookie is kept so the check stays solved. The wipe also covers the site storage (local
  storage, IndexedDB, service workers and the rest), which is the main reason a sign-in cannot
  survive, plus caches, shared storage, trust tokens, reporting data and the browser's own cache
  folder (the review at `9db9b10`).
- **A crash in the middle of a take-over is finished at the next start,** from a marker file. **The
  browser never returns to the bots until the clean-up has fully succeeded:** it stays in a
  `restoring` state and says why after 3 tries (the panel shows it; `fix/browser-restoring`).
  Why: a half-wiped profile must never be handed back to a bot.
- **Owner decision, 2026-11-16: the assistant's browser keeps no sign-ins.** After a take-over
  everything is wiped except the search engine's cookies. Why: a page can try to steer a bot, and a
  bot signed in to the owner's accounts could act in them; the owner chose the recommended, simpler
  option over lasting sign-ins. **This supersedes** the plan for a later "sign-in by take-over
  (low-value sites only)" phase; that phase is dropped from `plan.md`.
- A hand-back while a bot's turn is still running stops the assistant's headless browser mid-turn
  (rare and safe; noted by the review as a possible refinement).
- The search choice is therefore settled: DuckDuckGo, with its person-check solved by the owner
  through the take-over.

## 2026-11-16 — The window opens at login (merged, `feat/autostart-window` `c88784f`)

- **The window opens at login through one XDG autostart entry,** at the owner's request: the owner
  asked for the window to show up after every reboot. Why an autostart entry: it runs in the owner's
  desktop session with its display settings, a closed window stays closed until the next login (or
  the wake phrase, or `sasha window`), and the owner can switch it off in Startup Applications.
- **Attach mode never starts a second brain.** `sasha window --attach` waits for the brain service
  instead of starting its own, so two brains can never clash on one port. The check that the brain
  is up refuses redirects (a redirect counts as "not up"; a probe with a local server answering 302
  to a second server sent that second server 0 requests).
- The entry is written only by `sasha autostart on --yes`, which the manager ran on
  2026-11-16 after the merge; `sasha autostart off` removes it. See the new list of allowed writes
  outside the project in `team.md`.

## 2026-11-16 — Follow-ups to the browse helper and the take-over are merged (appended)

- **`fix/said-followups` (`c715fad`, merged as `49d14c7`) fixes the two open items** noted in the
  browse-helper entry above: a negated or excluded device is no longer counted as named ("turn the
  kitchen light off, not the bedroom" no longer passes the bedroom), and reminder text keeps the
  owner's negations ("remind me not to call the bank" no longer passes "call the bank"). Reviewed
  LGTM.
- **`fix/browser-restoring` (merged as `c640725`)** makes the browser panel show the clean-up after
  a hand-back (the `restoring` state named in the take-over entry above).
- **`feat/calc-tools` (merged as `86fde8e`):** calculate and convert tools with exact decimals,
  bounded, never `eval`.

## 2026-11-16 — Memory merged (`feat/memory-server` `a46a7a4`, `feat/memory-ui` `a552f46`; `main` at `fc10776`, 1555 passed; contract v0.6)

This is the built form of the memory design approved on 2026-11-15 (see that entry for the conditions).
The reviewer's last review of `a46a7a4` with `a552f46` was LGTM. The limits and defaults below (5 notes
per turn, 10 per 10 minutes from wake turns, about 1500 characters in the prompt, memory on or off per
bot, one shared household memory) are from the design note kept in the team folder; the reviews did not
list them as changed. Decisions, with reasons:
- **Notes are made only when the owner explicitly asks** ("remember that ...", "note that ...").
  No silent extraction and no suggested notes in v1. A note is kept in the first person, as said
  ("I take the 8:10 bus"), so the Memory list reads in the owner's voice; the prompt tells the model
  that "I", "me" and "my" in a note mean the owner. Why: memory is a lasting prompt channel, so only
  what the owner said may enter it.
- **Voice notes wait for Keep.** A note made on a wake-word turn is stored as pending, never goes into
  any prompt, and shows on the owner's screen ("added by voice: keep or delete") in the Memory screen
  and a card on Home. Only a kept note becomes active. Why: anyone in earshot can speak, including a
  television.
- **Wake turns read only notes marked "ok to say aloud".** Chat and window turns (the owner's own
  screen) get all active notes. A note the owner keeps from a voice turn is marked "ok to say aloud",
  because it was said aloud already. Why: a read on a wake turn is spoken into the room.
- **M3: every word and number in a note must come from the owner's own messages** (checked word for
  word). Nothing from a tool result, a web page, an email, a device name, or anything the model infers
  or summarises. Why: a write that is only mostly checked becomes a lasting injection. This is
  enforced by the check, not by the prompt.
- **Secrets are refused and masked.** Passwords, PINs, codes, card and account numbers, keys, tokens
  and long digit runs are neither stored nor left in the conversation's history. Why: a secret said
  once must not stay anywhere.
- **No silent eviction at 200 notes.** At the cap, new notes are refused and the reply says so ("my
  notes are full; ask me to forget something"); pending notes count. Why: a note the owner relies on
  vanishing is the memory version of a silent wrong time. At most 5 new notes per turn, and 10 per 10
  minutes from wake turns.
- **Forgetting is done by the server from structured fields, and it reports exactly what was removed.**
  There are three levels: by note ("forget that", by id or by words; if more than one note matches,
  it asks); by time window ("forget the last 5 minutes"), which removes the notes made in that window
  and also every open conversation's own turns from it (Home, the listener, every chat), so the model
  cannot quote them; and everything, only when the owner's message says "everything" or "all", and
  only after a confirm. By voice the number of minutes must have been said, so a made-up "60" cannot
  wipe an hour. Forgotten means gone: removed from the store and the prompt, nothing kept in a trash.
- **Wake turns cannot forget beyond their own voice notes.** Forgetting anything else, a time window or
  everything is refused by voice with "Do that on the screen, please." Why: the safer side, and no
  spoken confirm is needed on the wake path.
- **M1: a note can never cause a change.** On every turn of a bot with memory on, a changing call
  (device control, scenes, timer set, change or cancel) is allowed only when the owner's own words
  name both the target and the action; the action must be in the current message, and the target may
  come from the owner's last 2 messages within 3 minutes (never for locks, alarm panels or covers; see
  the relaxation entry above). Notes, tool results and the model's replies never count. Why: that is
  what makes a stored note harmless. For cancel and snooze, an item id from the model counts as named
  only when the owner's words name that very item (all of its label's words, or its kind when it is the
  only active one of that kind, or it is ringing now).
- **The memory section is shown to the model as data, never as instructions,** in a fixed-heading
  block, newest first and bounded to about 1500 characters; it is never part of the browse helper's
  context, and browser bots get no memory at all. Memory is on by default for the main assistant and
  the window, off for new bots.
- **Truthfulness:** a reply that claims "I'll remember that" or "I've forgotten that" when no memory
  tool succeeded gets the server's own line, as for timers; the history keeps what was really said.
- **Household:** one shared memory for now (one per person needs speaker identification, Phase 3).
- **Known limits (from the reviews):** on wake turns the target of a change may be taken from an
  earlier wake transcript, which anyone in the room could have said; locks and similar never carry a
  target over, and lights and media are low-harm. A review nit is open: when an item is ringing, its id
  passes even if the owner named a different item's label.
- **Not tried by the owner yet:** the Memory screen and the Home card for waiting voice notes, and a
  real voice note.
