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
  and pushes. The owner told Primary this directly on 2026-11-14; `CLAUDE.md` step 3 still says "once the owner says go" and is the owner's to update.
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

- PR #1 (Phase 0 skeleton + 3 review-fix commits) merged 2026-11-14, merge
  commit `9dfe959`. 86 tests pass; ruff, format and mypy strict are clean.
- Phase 0 gate still open: turning on a real light needs Docker + Home Assistant,
  which needs the owner (sudo). Not blocking the window goal.

## Tasks in flight

| Branch | Session | What | Status |
| --- | --- | --- | --- |
| `feat/brain-server` | Staff SDE | FastAPI brain server, WebSocket chat, bots store, connector registry, `serve` and `window` commands, laptop profile | merged and pushed after two review rounds (16 findings fixed); `main` is `c6e8b1e`, 170 tests pass; `sasha serve` and `sasha window` exist |
| `feat/web-ui` | Design expert | Face + home screen, bots screen, settings/connectors, mock mode | mock version done at `15a81d7`; face direction changed (see below); runs at 60 fps in a real window; two variants with the owner |
| `feat/voice-stages` | Jack of all tasks | faster-whisper STT and Kokoro TTS stages, fakes, bench script | passed review (LGTM at `59a9612`); being brought up to `main` and wired into the server; not merged yet |
| `docs/scope-refresh` | Grunt work | plan.md, decisions.md, linkedin-notes.md, README, .env.example for the new scope | merged and pushed (`6fbc60d`, 86 tests pass) |
| `docs/handoff` | Documentation engineer | this file, `team.md`, devlog; owner notes in `User_reference/` | merged and pushed (`main` is `5bd49eb`); work continues on this branch with new commits. Also owns `plan.md`, `decisions.md`, `linkedin-notes.md`, `README.md`, `.env.example` |

All three build branches (`feat/brain-server`, `feat/web-ui`, `feat/voice-stages`)
have reported.

Merge order: `feat/brain-server` first (others depend on its API), then
`feat/web-ui`, then `feat/voice-stages` (the server session wires the real
stages in after it lands), `docs/scope-refresh` any time. `pyproject.toml` and
`uv.lock` are touched by two branches; rebase the later one and re-lock.

## Agreed interfaces

- **API contract v0.1** (v0.2 will add adding and removing MCP connectors): a `speak=0|1`
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

## Risks

- **VRAM headroom on the 8 GB laptop card is about 1 GB** (LLM 5.2 GB at 8k context + STT
  0.8 GB = 7.1 of 8.15 GB). A bigger context could push LLM layers to the CPU.

## Next after these land

Our own design. In order:
1. Wire real STT/TTS into the server; check the full voice loop in the window.
2. MCP connectors, with an approval step before any tool that changes something.
3. Per-bot avatar and tool groups.
4. Wake word ("Hey <name>") in the window, then Marathi (Phase 2).
5. A browser-control bot with live view and take-over.
6. A safe "open an app or page on this device" tool, allowlisted.

## Pending doc follow-ups

- When `feat/voice-stages` merges: the README voice licence rows (held as `efa3cc4` on the
  local branch `docs/readme-licences`) and the voice commands in `HOW_TO_RUN.md`.

## Ideas from an earlier project

Ideas from an earlier project of the owner's: read-only, no copying (owner's decision, 2026-11-14; copying was first allowed, then withdrawn the same evening). Nothing was copied.

## Waiting on the owner

- Docker install (sudo) for Home Assistant, then the HA account and `HA_TOKEN` in `.env`.
- What "Grok-bot-like" should mean beyond "several bots with their own chats".
- Whether Claude's memory folder for this project is allowed (see the folder rule).
  Memory notes are paused until the owner says so.
- Which smart-display and TV devices to support.
- Whether to keep eyes as a switch on the face (the default is without eyes).
- `CLAUDE.md` "Current phase" says to stop and check with the owner before adding voice.
  Only the owner changes that line. `CLAUDE.md` step 3 ("once the owner says go") also
  differs from how merging works now; the owner updates it.
- **GPL text-to-phoneme packages for the English voice.** The voice is Kokoro-82M via
  `kokoro-onnx` (weights Apache-2.0, `kokoro-onnx` MIT). It depends on two GPL-3.0
  packages, `phonemizer` and `espeakng-loader` (bundles espeak-ng). Tonight's
  decision: keep them only in the optional `voice` extra, imported lazily, nothing
  GPL vendored or committed. Open question: keep them as an optional install, or
  spend time on a GPL-free path (lexicon-based G2P plus our own small ONNX runner,
  worse on unknown words)?

## How to resume if the manager session is new

1. Read this file, `plan.md`, `decisions.md`, newest `devlog/`.
2. `git fetch && git worktree list && git branch -a` to see every branch.
3. Ask each session (by name) for a status report; update the table above.
4. Continue the flow: review → fix → merge in the order given.
