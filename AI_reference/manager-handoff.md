# Manager handoff (read this first to resume the project)

Kept by the manager session ("Primary"). Updated whenever a task is assigned,
reported, reviewed or merged. If the manager session is lost, a new session can
pick up from this file plus `plan.md`, `decisions.md` and the newest devlog.

Last updated: 2026-11-14, late evening.

## How the project is run

- The owner set up a team of local Claude sessions on 2026-11-14. Each has one role.
- **Primary (manager):** plans, writes task briefs, sends branches to review,
  merges to `main`, pushes. Does not write product code.
- **Staff SDE:** main coder. **Design expert:** face, UI, anything people see.
  **Jack of all tasks:** flexible helper (research, side modules).
  **Grunt work:** small, cheap tasks (docs, config). **Code review session:** reviews.
  **Documentation engineer:** keeps this file, the daily devlog, `team.md` and the
  owner's private `User_reference/` notes current. See `team.md` for the full recipe.
- Hub and spoke: every session talks only to Primary. No session-to-session messages.
- Each worker has its own git worktree under `Dependencies/worktrees/<branch>`
  (git-ignored) and its own `.venv` there. The main checkout is Primary's.
- Flow per task: brief → worker commits on its branch → Primary sends the branch
  to the reviewer → worker fixes findings → reviewer approves → Primary merges
  and pushes. The owner decided this on 2026-11-14, and `CLAUDE.md` step 3 now says so.
  Nothing is posted on GitHub except the pushed commits.
- **Strict rule (owner, 2026-11-14):** no session changes anything outside the
  project folder. Reading elsewhere is fine. Caches and downloads go under
  `Dependencies/`, a browser profile under `Data/`. Details in `team.md`.
  Before this rule, some sessions wrote to the default uv cache in the home directory
  and to Claude's own memory folder for this project. Nothing was deleted.
- **Secrets (owner, 2026-11-14, this project only):** sessions are allowed to access the
  project's secrets file for now, because it holds nothing sensitive yet. Team practice is
  narrower: code and tests load it at run time; sessions don't open, print, search, copy
  or paste the file or any secret value. If a rule or hook blocks a read, stop and tell
  Primary. Full text in `team.md`.
- Shared team files (git-ignored, local): `Dependencies/team/team-rules.md` and
  `Dependencies/team/api-contract.md`. The contract is also committed as
  `AI_reference/api-contract.md` once the server branch lands.

## Current goal (set 2026-11-14)

A window on the laptop dev box that shows the assistant's face. The owner talks
to it (voice, with typed chat as fallback) and it answers by voice, all on local
AI. A button opens a "bots" area (several bots, each with its own model,
instructions and connectors) and a settings screen with connectors. This device
only for now; satellites on other devices come later.

## State of `main`

- `main` is `e233db4` (2026-11-14, night). 484 tests.
- **Wake word:** all three parts are on `main` (`feat/wake-server` at `f0ce325`, three review
  rounds). The window runs from `main` with `health.wake.available` true. The wake word has not
  run on a real microphone: real-microphone checks are scheduled with the owner. **Rule: no
  real-microphone run without the owner present** (the microphone is opened only with the owner
  at the machine).
- Also merged: `fix/tests-shared-temp` and `fix/tests-temp-nits` (a test failed when two runs
  shared a checkout; each run now has its own temp folder).
- In progress: `fix/wake-truthful-reply` (the server speaks a fixed factual sentence when a device
  change was refused, unconfirmed or failed, or when the model claims a change with no successful
  call; a gate for switching on device control by voice; a small clock tool), `feat/browser-core`
  (first browser parts; address guard verified on the real Chrome; two blocking review fixes),
  `fix/listener-confirm-race` (a listener test that failed under load is being checked for a real
  ordering problem).
- **Window runs from `main`; goal reached for typed input.** `sasha window` was started on
  the laptop. The server came up with voice on (`stt` and `tts` true), the window's page
  connected, and a typed question on a `speak=1` socket was answered in text and speech:
  first text 164 ms, first audio 1.4 s, 50 tokens/s, LLM fully on the GPU.
- **Not verified:** the face on screen (nobody has looked; no screenshot tool on the
  machine) and a real microphone (no real person has spoken into one yet).
- Merged today: Phase 0 skeleton (PR #1), the brain server, voice stages, the window, the
  docs batches and the follow-up fixes listed below.
- `fix/stt-runtime-fallback` merged (`3f76eee`): speech-to-text falls back to CPU for a
  request when the GPU fails.
- Phase 0 gate still open: turning on a real light needs Docker + Home Assistant,
  which needs the owner (sudo). Not blocking the window goal.

## Tasks in flight

| Branch | Session | What | Status |
| --- | --- | --- | --- |
| `feat/brain-server` | Staff SDE | FastAPI brain server, WebSocket chat, bots store, connector registry, `serve` and `window` commands, laptop profile | merged and pushed after two review rounds (16 findings fixed); `main` is `c6e8b1e`, 170 tests pass; `sasha serve` and `sasha window` exist |
| `feat/web-ui` | Design expert | Face + home screen, bots screen, settings/connectors, mock mode | merged and pushed after three review rounds: particle orb with four states, Home with subtitles, history drawer and facts strip, Bots with up to three chat tabs, Settings with connector cards, mock mode |
| `feat/voice-stages` | Jack of all tasks | faster-whisper STT and Kokoro TTS stages, fakes, bench script | merged and pushed (`b53a118`); wired into the server |
| `fix/server-followups` | Staff SDE | window and Home Assistant error hardening | merged and pushed (`99ae0ac`) |
| `fix/server-followups-2` | Staff SDE | dead proxy only on closed privileged ports; a malformed Home Assistant address becomes a warning; `/api/models` lists only chat-capable models with a `tools` flag, capability checks bounded to about 3 s; `health.limits.max_sockets`; the window waits up to 90 s on a cold start with progress lines; contract v0.1.1 | merged and pushed |
| `chore/env-tmpdir` | | temp files inside the project | merged and pushed (`5aa4ee5`) |
| `chore/env-tmpdir-notes` | | `env.sh` refuses to run outside a bash checkout of the repo and removes temp entries older than 7 days | merged and pushed (`f2bfae5`) |
| `fix/warmup-timeouts` | | short connect timeout for the model warm-up; voice turns record the STT and TTS model versions in telemetry | merged and pushed |
| `fix/stt-runtime-fallback` | | speech-to-text falls back to CPU for a request when the GPU fails | merged and pushed (`3f76eee`) |
| `feat/wake-server` | Staff SDE | brain side of the wake word: gate, abuse limits, reserved socket, contract v0.2 | merged and pushed (`f0ce325`) after three review rounds |
| `fix/wake-truthful-reply` | | truthful spoken replies for refused or failed device changes; gate for voice device control; clock tool | in progress, not merged |
| `feat/browser-core` | | the assistant's own browser, first parts | in progress; two blocking review fixes |
| `fix/listener-confirm-race` | | a listener test that failed under load | in progress |
| `feat/wake-listener` | Jack of all tasks | `sasha listen` (always-on listener), `sasha autostart` | merged and pushed after two review rounds; follow-up `fix/wake-listener-notes` merged |
| `feat/wake-indicator` | Design expert | tray icon and chimes | merged and pushed; the send guard is stricter (exemptions pinned to exact lines, mail-sending calls forbidden in every file) |
| `docs/scope-refresh` | Grunt work | plan.md, decisions.md, linkedin-notes.md, README, .env.example for the new scope | merged and pushed (`6fbc60d`, 86 tests pass) |
| `docs/handoff` | Documentation engineer | this file, `team.md`, devlog; owner notes in `User_reference/` | merged and pushed (`main` is `5bd49eb`); work continues on this branch with new commits. Also owns `plan.md`, `decisions.md`, `linkedin-notes.md`, `README.md`, `.env.example` |

Merge order for what is open: Primary decides per branch. `pyproject.toml` and `uv.lock`
are touched by several branches; rebase the later one and re-lock.

## Agreed interfaces

- **API contract v0.2 (wake word) is written;** the server and listener sessions build against
  it. The MCP connector endpoints (add and remove) move to planned v0.3. For voice requests,
  volume is capped at 0.6. Base v0.1: a `speak=0|1`
  parameter on the WebSocket (Home speaks every answer, bot chat tabs are silent); one
  conversation per socket; `PUT` connector returns the Connector; `pronouns` is an
  object; `voice.tts_languages`.

- **TTS stage:** `stream(text, language)` yields one audio chunk per sentence,
  24 kHz output. Agreed through Primary by the voice and server sessions (2026-11-14). The server
  resamples as needed for the WebSocket contract.

## Face direction (2026-11-14)

The owner rejected the first face and any human character. The owner asked for a friendly
living ball of light with elemental brush-art flair: an orb of light developed into a
character, with a brush-art flourish per state (water listening, wind thinking, flame
speaking), drawn from scratch. Two variants were with the owner: without eyes, and with
two eyes of light.

Update: the owner found the painted ribbons wrong and asked for particles. The flair is now
flowing particles in the orb's own style: mist that breathes at idle, particles drawn in
when listening, currents when thinking, rising embers when speaking. Default is without
eyes; eyes stay as a switch.

## Decided by the owner (2026-11-14)

- The wake phrase is the name alone, read from config.
- Voice control is read-only by default; after the owner switches on device control for voice
  once in the Bots screen, limits apply (see `decisions.md`).
- A session may create the start-at-login link, under the rule in `team.md`.
- Device control by voice, after the one opt-in: a spoken yes for "all", any scene, or more
  than 3 distinct devices in one turn (counted across all its calls).

- The listener does not listen while the screen is locked.
- Sessions may access the project's secrets file for now (code and tests load it at run time; sessions never open, print, search, copy or paste it or any value; a block means stop and tell Primary). The owner tells Primary
  when that stops.
- Claude's own memory folder for this project may be used for notes.
- The GPL text-to-phoneme question stays open; features come first.
- The real-light test (Docker and Home Assistant) is postponed for a few days.
- `CLAUDE.md` "Current phase" (Phase 1, Phase 0 gate open and postponed) and step 3 (the
  manager merges after the reviewer approves) were edited on the owner's instruction. The
  phase-gate checkpoint ("stop and show the owner at each phase gate") is kept in `CLAUDE.md`,
  added in `59e21a7` after the reviewer's note on `4e71b41`. Both `CLAUDE.md` commits were made
  on `main` at the owner's instruction and reviewed afterwards.

## Risks

- **Limits of the wake word v1:** anyone in earshot or a television can trigger it. A false
  wake on the name alone makes the next sentence a command. In a fullscreen app only the
  sound shows a wake.

- **Load order decides GPU placement.** With speech-to-text on the GPU first, the LLM
  spilled 17% onto the CPU (36 tokens/s). With the LLM first everything fits (7.35 of
  8.15 GB, 58 tokens/s). The server warms the LLM before loading the voice stages and
  keeps it loaded for 24 h. Known limit: a reload after the keep-alive expires can split
  it again.
- **Rule: the microphone is never open without a visible sign.** Leaving Home, hiding the
  window or closing the page closes it; a start in progress can be cancelled; repeated Talk
  presses open one microphone.
- **One voice brain on the GPU at a time for the team.** Two would not both fit in 8 GB.

- **VRAM headroom on the 8 GB laptop card is about 1 GB** (LLM 5.2 GB at 8k context + STT
  0.8 GB = 7.1 of 8.15 GB). A bigger context could push LLM layers to the CPU.

## Next after these land

Build order set by the owner on 2026-11-14 (night). Our own design.

- **First week: software only.** Device and robot work comes after that: Home Assistant with real
  devices, cameras, pet devices, room-to-room announcements, satellites on other devices and the
  robot body (all "device work, later"). Why: device work needs hands-on testing, scheduled after
  the first week.
- **No "fun" features** (games, trivia, stories, a music visualiser).
- In flight next to Marathi: the assistant's own browser (own profile inside the project, live
  view, take over, approval before anything that changes something, no access to the local
  network; voice-started requests are read-only there). Ideas from an earlier project of the
  owner's only; own code.
- Check the full voice loop and the wake word on a real microphone (checks are scheduled with the owner).

In order, unless the owner changes it:

1. Marathi–English support (Phase 2). A facts-first research task is running (models, licences,
   which are gated and need the owner's acceptance, sizes, CPU feasibility); design follows.
2. Timers, alarms and reminders.
3. Memory ("remember that...").
4. A morning brief. It needs calendar and mail, so the parked send-guard question is asked
   before the mail part.
5. Media control for whatever is playing.
6. Questions about the owner's own files (local search).
7. Scheduled routines.
8. "What's on my screen" with a vision model.
9. Computer helpers: open apps and sites, dictation, local meeting and voice-note
   transcription, clipboard summary.
10. Web search with a spoken summary (through the assistant's own browser).
11. Calculation tools.
12. Speaker ID with per-person profiles.
13. Lists.
14. Proactive reminders.
15. MCP connectors with approvals, bot templates, and multi-step tasks with a plan.
16. An activity log, "forget the last 5 minutes", and a visible everything-is-local status.

Four items on this list handle very private data. Each gets a short design note and a review before
any code, like the wake word and the browser: questions about the owner's files, "what's on my
screen", dictation and meeting or voice-note transcription together with the clipboard helper, and
speaker profiles. Each note says which folders or screens, what is stored and for how long, the
visible sign, and how to turn it off.

## Pending doc follow-ups

- None right now.

## Ideas from an earlier project

Ideas from an earlier project of the owner's: read-only, no copying (owner's decision, 2026-11-14; copying was first allowed, then withdrawn the same evening). Nothing was copied.

## Waiting on the owner

- Docker install (sudo) for Home Assistant, then the HA account and `HA_TOKEN` in `.env`
  (postponed by the owner; device work comes later).
- What "Grok-bot-like" should mean beyond "several bots with their own chats".
- Which smart-display and TV devices to support.
- Whether to keep eyes as a switch on the face (the default is without eyes).
- **GPL text-to-phoneme packages for the English voice.** The voice is Kokoro-82M via
  `kokoro-onnx` (weights Apache-2.0, `kokoro-onnx` MIT). It depends on two GPL-3.0
  packages, `phonemizer` and `espeakng-loader` (bundles espeak-ng). Tonight's
  decision: keep them only in the optional `voice` extra, imported lazily, nothing
  GPL vendored or committed. Open question: keep them as an optional install, or
  spend time on a GPL-free path (lexicon-based G2P plus our own small ONNX runner,
  worse on unknown words)?

## Ask the owner before...

- **When the Marathi research lands:** the owner will probably need to accept terms on some model
  pages and put a Hugging Face token into the secrets file personally. Details to follow.

- **Before Gmail work merges (Phase 4):** is the narrow send-guard exception acceptable, or must
  the rule be absolute? The owner parked this on 2026-11-14; the exception stays as reviewed
  until then. Primary asks again at that gate.

## How to resume if the manager session is new

1. Read this file, `plan.md`, `decisions.md`, newest `devlog/`.
2. `git fetch && git worktree list && git branch -a` to see every branch.
3. Ask each session (by name) for a status report; update the table above.
4. Continue the flow: review → fix → merge in the order given.
