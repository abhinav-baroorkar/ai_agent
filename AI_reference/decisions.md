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

