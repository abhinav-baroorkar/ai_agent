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
  and pushes. Nothing is posted on GitHub except the pushed commits.
- **Strict rule (owner, 2026-11-14):** no session changes anything outside the
  project folder. Reading elsewhere is fine. Caches and downloads go under
  `Dependencies/`, a browser profile under `Data/`. Details in `team.md`.
  Before this rule, some sessions wrote to the default uv cache in the home directory
  and to Claude's own memory folder for this project. Nothing was deleted.
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
| `feat/brain-server` | Staff SDE | FastAPI brain server, WebSocket chat, bots store, connector registry, `serve` and `window` commands, laptop profile | assigned 2026-11-14 |
| `feat/web-ui` | Design expert | Face + home screen, bots screen, settings/connectors, mock mode | assigned 2026-11-14 |
| `feat/voice-stages` | Jack of all tasks | faster-whisper STT and Kokoro TTS stages, fakes, bench script | assigned 2026-11-14 |
| `docs/scope-refresh` | Grunt work | plan.md, decisions.md, linkedin-notes.md, README, .env.example for the new scope | done (`af6541e`, 86 tests pass); two fix commits (`82544a8`, `b8e1b7e`) answer all eight review findings; in re-review |
| `docs/handoff` | Documentation engineer | this file, `team.md`, devlog; owner notes in `User_reference/` | `01530a7`; kept current |

Merge order: `feat/brain-server` first (others depend on its API), then
`feat/web-ui`, then `feat/voice-stages` (the server session wires the real
stages in after it lands), `docs/scope-refresh` any time. `pyproject.toml` and
`uv.lock` are touched by two branches; rebase the later one and re-lock.

## Agreed interfaces

- **TTS stage:** `stream(text, language)` yields one audio chunk per sentence,
  24 kHz output. Agreed by the voice and server sessions (2026-11-14). The server
  resamples as needed for the WebSocket contract.

## Next after these land

Our own design, with ideas and code from an earlier project of the owner's. In order:
1. Wire real STT/TTS into the server; check the full voice loop in the window.
2. MCP connectors, with an approval step before any tool that changes something.
3. Per-bot avatar and tool groups.
4. Wake word ("Hey <name>") in the window, then Marathi (Phase 2).
5. A browser-control bot with live view and take-over.
6. A safe "open an app or page on this device" tool, allowlisted.

## Pending doc follow-ups

- After `docs/scope-refresh` merges: `team.md` says "source `AI_Agent/scripts/env.sh`"
  in place of the manual cache exports. On that branch the script is safe to source
  from a worktree: caches go to the main root's `Dependencies/`, and only the main
  checkout shares `Dependencies/.venv`.
- After `feat/brain-server` is on `main`: update stale facts in `CLAUDE.md` (current
  phase, profile list, `server/` package, commands) and `User_reference/HOW_TO_RUN.md`.
  Leave the workflow and merge rules in `CLAUDE.md` as they are.

## Reuse of earlier code

The owner allows reusing code from an earlier project of his. Rules for doing it are in the team rules. Copying happens only when a task brief says so.

## Waiting on the owner

- Docker install (sudo) for Home Assistant, then the HA account and `HA_TOKEN` in `.env`.
- What "Grok-bot-like" should mean beyond "several bots with their own chats".
- Whether Claude's memory folder for this project is allowed (see the folder rule).
  Memory notes are paused until he says so.
- Which Meta device he has; the TV model.
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
