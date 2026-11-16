# Manager handoff (read this first to resume the project)

Kept by the manager session ("Primary"). Updated whenever a task is assigned,
reported, reviewed or merged. If the manager session is lost, a new session can
pick up from this file plus `plan.md`, `decisions.md` and the newest devlog.

Last updated: 2026-11-15, evening (resume section added for a possible pause in work).

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

## Resume here: work in flight (2026-11-15, evening)

Written so work can restart cleanly if sessions stop (a usage limit is close). `main` is `f316d6f`.
Branch heads below are the commits named at the time of writing; check `git log` before relying on one.

**Reviewer's queue, in merge order**

| # | Branch | Head | Note |
| --- | --- | --- | --- |
| 1 | `feat/timers-server` | `d37cc0a` | the head moved on from `b253796`, which had one blocking item (a false claim spoken when a reply ends with a question; a fix was in progress); the new head is not yet re-reviewed here |
| 2 | `fix/timer-days` | `5fb66d3` | on top of timers |
| 3 | `feat/autostart-window` | `139741a` | the window opens at login through one autostart entry; attach mode never starts a second brain |
| 4 | `docs/handoff` | this branch | `decisions.md` and `README.md` changes only; the rest is routine |
| 5 | `fix/style-mock` | `1cd618e` | |
| 6 | `fix/narrow-topbar` | `fc9e972` | |
| 7 | `fix/browser-turn-holder` | `c56b234` | the two browser follow-ups (frame cap, one bot at a time) |
| 8 | `feat/browser-takeover` | `b4ee952` | the take-over: the owner drives the assistant's browser in a visible window |
| 9 | `feat/browse-helper` | `a029186` | a `browse` helper so the main assistant can browse; Jack of all tasks is adding changes to the spoken-claim guard for memory (`earlier`, `always_guard`, `Tool.said_checked`) |
| 10 | `feat/browser-ui-v05` | `85dddca` | |
| 11 | `feat/calc-tools` | `1326899` | calculation tools |
| 12 | `feat/memory-server` | `3b27083` | memory on the server; it cannot merge without the M1 no-change guard, which comes after the helper merges |

**Known merge conflicts:** timers vs helper (5 files; merge timers first); take-over vs
autostart-window (`app/shell.py`); take-over vs timers (`deps.py`); helper vs calc (`deps.py`).

**Design only (no code):** `media-design.md` (media control through MPRIS). Owner defaults: a
`wpctl` fallback capped at 0.6, no titles in v1, "stop" means pause.

**In progress:** the Design expert on `feat/memory-ui`, against contract v0.6.

**Waiting on the owner**
- The visible check of the app window.
- One read-only MPRIS introspection the next time the owner plays music (for the media design).
- The real-light test (postponed).
- Wake device control, after a few days of real-room use with telemetry.

**After the merges:** the manager runs `sasha autostart on --yes` (the window entry).

**Documentation held until merges** (to write when Primary says the branch merged; `decisions.md`
changes are structural and go to the reviewer):
- When `feat/browse-helper` merges, append to `decisions.md`, each with its reason:
  - B4 wording: a bot never holds raw browser tools together with any connector that reads private
    data or changes things. A bot with the direct browser has the browser only. Any other bot
    browses only through the `browse` helper, whose context has the browser tools and nothing else.
    Why: a web page can contain hidden instructions; keeping the browser in its own context means a
    page can only affect a lookup.
  - Web lookups go to the main assistant only, whenever the browser connector is on in Settings; no
    per-bot switch yet. Why: one place to get right first.
  - Wake-word turns get no browsing. Why: the room can't see the screen, so a voice turn can't show
    what was read, and spoken input is the easiest to spoof (a television, visitors).
  - P1 taint rule: every tool counts as changing unless marked read-only. In the same turn as a
    successful lookup, no changing tool runs. While a looked-up turn is still in context, a device
    change runs only if the owner's current message names the device and the action; anything else,
    including a bare "yes", is refused. Why: the model's next question might be one a page planted;
    "yes" doesn't say which device or action.
  - Spoken lookup answers are capped at 500 characters and end with "From <site>." from the
    server's own record; chat tabs show a "looked up" line instead. Why: spoken answers can't be
    skimmed, and the source must always be heard and not be fakeable by a page.
  - The helper's searches may use only the owner's current words, the task, and words from pages it
    has read. Why: a page must not be able to make the helper search for things never asked about.
- When `feat/browser-takeover` merges, append: the owner can drive the assistant's browser in a
  visible window for up to 15 minutes; on hand-back, downloads, form data, logins and history are
  wiped and only the search engine's cookies are kept. Why: the owner sometimes has to solve a
  person-check or sign in; 15 minutes bounds it; the wipe means nothing typed stays for a bot to
  see; the search-engine cookie is kept so the check stays solved.
- When `feat/autostart-window` merges: append a `decisions.md` entry (the window opens at login
  through one XDG autostart entry, at the owner's request; attach mode waits for the brain service
  instead of starting its own, so two brains can never clash on one port); change the `team.md`
  section "One allowed write outside the project" into an "Allowed writes outside the project"
  list, keeping dates (each made only by a `sasha` command that the manager runs after the feature
  is merged, never by a session directly; each generated file carries a marker, and `off` removes
  only marked files): the systemd user unit links for the brain and the listener (2026-11-14); the
  autostart entry for the window (`sasha autostart`, 2026-11-15); the application-menu entry
  (`sasha app-menu`, allowed 2026-11-15 with the app shell, not yet installed); the memory folder
  under the home Claude folder for the manager's notes. `team.md` is structural.
- Private `User_reference/HOW_TO_RUN.md`: a "Start at login" part when the autostart branch merges
  (`sasha autostart on` shows what it sets up; `sasha autostart on --yes` does it; from the next
  login the brain and the wake listener start in the background and the window opens by itself,
  showing a starting line for a few seconds, then Home; closing it keeps it closed until the next
  login, the wake phrase, or `sasha window`; undo with `sasha autostart off` or turn the window part
  off in the desktop's Startup Applications; while start-at-login is on, don't also run `sasha
  window` or `sasha listen` in terminals).

## State of `main`

- `main` is `f316d6f` (2026-11-15, evening; all pushed). Tests on `f316d6f`: 840 passed, 5 skipped,
  2 deselected (the real-Chrome file). API contract is v0.4.4 (bots carry a breathing style). Merged since `5e89c34` (details in `devlog/2026-11-15.md`):
  Talk ends by itself; plain-text spoken replies and real-ability prompts; wake phrase "hey" plus
  the name with a speech-to-text hint; truthful wake answers; the app window (Qt WebEngine, optional
  `app` extra, Chrome window as fallback with `sasha window --chrome`, opened by the wake phrase);
  the "Your bots" page (Concept A); the persona offers only what its tools can do; eight breathing
  styles per bot; spoken-text and address cleanups. Also merged since `6a2c8f9`:
  `fix/voice-waiting` (`fa72e69`, `main` `9cbe02f`): send-now while a previous answer is still
  stopping sends the recording once the server is free; leaving Home, hiding or closing drops a
  queued or unstarted recording and sends no stray cancel; the style picker shows Glow for a value
  that is not a style. `chore/review-followups` (`36f5e01`, `main` `f316d6f`): private-name bench
  runs store no transcript; the tray test pins the fast path; `Bot.style` is typed `BotStyle`.
- **In review:** see "Resume here" above (the queue is now long; timers is first).
- **Browser work resumed** (the Jack of all tasks session): `fix/browser-turn-holder` first, then
  the take-over, then the browse helper so the main assistant can browse.
- **Not verified:** the app window has not been looked at on screen (the owner's visible check is
  still to come); the wake word on a real microphone in the new window; timers with real voice.
- **Memory:** the design is approved with conditions and is not built (see `decisions.md`).
- **Wake word:** all three parts are on `main`. The window runs from `main` with
  `health.wake.available` true. The wake word has not run on a real microphone: real-microphone
  checks are scheduled with the owner. **Rule: no real-microphone run without the owner present**
  (the microphone is opened only with the owner at the machine).
- **Wake turns are truthful** (`fix/wake-truthful-reply`, `61aa277`; `fix/wake-request-phrases`,
  `d5c9c43`): on a wake turn the server holds the model's text for a change request and states what
  was and wasn't changed from the tool results. Device control by voice can't be switched on
  without this. A read-only clock tool is available to every bot. A bench of 42 request phrasings
  is a gate in the tests.
- **Timers, alarms and reminders** (first feature of the build order): the Home timers card is
  merged (`feat/timers-ui`, `6c0a4b3`, contract v0.3; Escape never dismisses an alarm). The server
  part, `feat/timers-server`, is in its final review round.
- **The assistant's own browser, phase 1, is merged** (`feat/browser-core` `96636db`,
  `feat/browser-wiring` `deda9ae`, `feat/browser-live-view` `c462083`, `feat/browser-ui` `011b6f5`,
  then `fix/browser-panel-followups` `deb6b09` and `fix/browser-view-followups` `39af3f3`). What
  it is: see "The assistant's own browser" below.
- **Listener fixes merged:** `fix/listener-confirm-race` (`bda9732`) and `fix/listener-followups`
  (`4968ef5`). A test that failed now and then turned out to be a real bug (see "Risks").
- Also merged: test-only fixes (`fix/tests-shared-temp`, `fix/tests-temp-nits`,
  `fix/wake-socket-test-race`).
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
| `fix/talk-auto-stop`, `fix/spoken-text`, `fix/chat-behaviour`, `fix/spoken-followups`, `fix/spoken-urls` | | Talk ends by itself; plain-text spoken replies; clock only when asked; bounded spoken-text cleaner; no address read aloud | merged and pushed (2026-11-15) |
| `fix/wake-real-mic`, `fix/wake-answers`, `fix/wake-hint-armed` | | wake phrase "hey" plus the name with a speech-to-text hint; wake turns answer the question; no hint on the armed follow-up | merged and pushed (`f5581ab`, `3a11e76`, `0d4ff75`) |
| `feat/app-shell`, `fix/app-shell-hardening`, `fix/entrance-calm` | | the assistant's own window (Qt WebEngine, optional `app` extra), hardening, calmer entrance | merged and pushed (`895f8ff`, `0c440b7`, `06e5036`); not yet looked at on screen |
| `feat/bots-redesign`, `fix/persona-abilities`, `feat/bot-styles`, `fix/style-lookup`, `feat/bot-style-field` | | "Your bots" page, abilities in the persona line, eight breathing styles, the style as a bot field (contract v0.4.4) | merged and pushed (`93d6b07`, `40cc959`, `860539b`, `214f41c`, `6a2c8f9`) |
| `chore/bench-extra-soundalikes`, `chore/test-robustness` | | private sound-alike names for the bench; failing tests name themselves | merged and pushed (`15ad0c1`, `adaa220`) |
| `fix/voice-waiting` | | send-now while a previous answer is stopping; dropping a queued recording; style picker fallback | merged and pushed (`fa72e69`) |
| `chore/review-followups` | | private-name bench stores no transcript; tray test pins the fast path; `Bot.style` typed | merged and pushed (`36f5e01`) |
| `fix/browser-turn-holder`, take-over, browse helper | Jack of all tasks | the browser follow-ups, then the take-over, then the browse helper so the main assistant can browse | in progress, in that order |
| `fix/wake-truthful-reply` | | truthful spoken replies for refused or failed device changes; gate for voice device control; clock tool | merged and pushed (`61aa277`); `fix/wake-request-phrases` (`d5c9c43`) added more request phrasings and a 42-phrase bench as a gate |
| `feat/browser-core`, `feat/browser-wiring`, `feat/browser-live-view`, `feat/browser-ui`, `fix/browser-panel-followups`, `fix/browser-view-followups` | | the assistant's own browser, phase 1: core, tools, live view in the Bots screen, follow-ups | merged and pushed (`96636db`, `deda9ae`, `c462083`, `011b6f5`, `deb6b09`, `39af3f3`) |
| `fix/listener-confirm-race`, `fix/listener-followups` | | the tray sign could disagree with the capture process | merged and pushed (`bda9732`, `4968ef5`) |
| `feat/timers-ui` | | Home timers card | merged and pushed (`6c0a4b3`) |
| `feat/timers-server` | | timers, alarms and reminders on the server | in review at `b253796`; one blocking item open (a false claim could be spoken when the reply ends with a question); not merged |
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

## The assistant's own browser (phase 1, merged)

- **How it runs:** the installed Chrome, driven over a DevTools pipe (never a TCP debug port), with
  its own marked profile under `Data/`, headless and muted. All traffic goes through our own
  filtering proxy that refuses loopback, private, link-local, metadata and mapped addresses
  (measured on the real Chrome: every tested form refused, also from page sub-resources).
- **Tools:** search, open, read, follow links, switch tabs. An address may be opened only if the
  owner wrote it in this conversation (the server rebuilds it). A leak check on search queries
  catches emails, long numbers and identifiers from earlier messages.
- **Limits:** a browsing bot can have no other connector and no timer tools (the one-bot-at-a-time
  rule has an open follow-up, see the live view line). No browser on wake turns. History files are wiped whenever the browser stops, only inside a profile the assistant
  created (marker file), and never through a link or under a live Chrome.
- **Live view** in the Bots screen: frames as JPEG at most about 5 a second, a site line that
  comes from the brain, Stop always available. No picture route over HTTP (contract v0.4.1).
  Two follow-ups are in progress on `fix/browser-turn-holder`: a page that reloads itself can
  exceed the frame cap, and two bots can share the browser within one turn.
- **Measured (the author's bench, pending the reviewer's own check):** injection bench with the
  real model, 8 kinds of trap page, 24 of 24 on the final run, 0 leaks to the network (3 runs per
  case, a small sample). An early run did leak an email through a search, which is why the
  leak check exists and why the guards don't rely on the model. See `research-notes.md`.
- **Next phase: take-over** (decided 2026-11-15): the owner solves the search engine's bot
  challenge personally, through a visible window; the brain is disconnected while the owner drives;
  hand-back by restart. Never automated, never worked around. Design review before code.
- **Later phases:** acting with approvals, sign-in by take-over (low-value sites only), media.

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

- The wake phrase is "hey" plus the name, read from config (2026-11-15; replaces the earlier "name alone": with a hint, recognition went from 65 to 102 of 108 synthetic samples).
- Start-at-login is turned on, at the owner's request.
- The window becomes its own app (Qt WebEngine via PySide6, optional `app` extra, LGPL); the Chrome window stays as the fallback until the owner's visible check. The wake phrase opens or raises it (once per 30 s, 6 an hour, never when locked).
- Bots area: Concept A (a bots page with cards, one labelled entry on Home), with a microphone in bot chats.
- Eight per-bot breathing styles (glow, ember, tide, gale, storm, mist, bloom, stone), glow by default.
- Memory design approved with conditions (see `decisions.md`); not built.
- Voice control is read-only by default; after the owner switches on device control for voice
  once in the Bots screen, limits apply (see `decisions.md`).
- A session may create the start-at-login link, under the rule in `team.md`.
- Device control by voice, after the one opt-in: a spoken yes for "all", any scene, or more
  than 3 distinct devices in one turn (counted across all its calls).

- The listener does not listen while the screen is locked.
- The search engine for the browser is DuckDuckGo. Its bot challenge is solved by the owner
  through a take-over of the assistant's browser, never automated or worked around; the take-over
  moves ahead of the other browser phases (2026-11-15).
- "Wake me at 7" asks whether that means morning or evening.
- The wake word's real-microphone checks are scheduled with the owner.
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
  wake on the wake phrase makes the next sentence a command. In a fullscreen app only the
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

- **A test that fails now and then is a possible product bug until shown otherwise.** One
  listener test failed under load. The cause was real: while the capture process was starting,
  another part of the loop could redraw the tray icon as "microphone closed", so the listener
  could record for up to 2 s under a closed icon. A matching case on close was found in review.
  Fixed so the sign counts a capture as open from the moment it starts until it is killed and
  reaped, checked by a test that tracks the real child process. No real microphone had been
  opened. The manager's pre-push check now shows the names of failing tests and stops on any
  failure (one merge had been pushed on a run with one failure).
- **Not tested for real yet:** the wake word on a real microphone, the browser on a public site,
  search on the real engine, and timers with real voice. The search engine (DuckDuckGo) served the headless browser a bot
  challenge; the owner will solve it through the take-over, which is being designed.
- **VRAM headroom on the 8 GB laptop card is about 1 GB** (LLM 5.2 GB at 8k context + STT
  0.8 GB = 7.1 of 8.15 GB). A bigger context could push LLM layers to the CPU.

## Next after these land

Build order set by the owner on 2026-11-14 (night). Our own design.

- **First week: software only.** Device and robot work comes after that: Home Assistant with real
  devices, cameras, pet devices, room-to-room announcements, satellites on other devices and the
  robot body (all "device work, later"). Why: device work needs hands-on testing, scheduled after
  the first week.
- **No "fun" features** (games, trivia, stories, a music visualiser).
- The assistant's own browser: phase 1 (read-only, live view) is merged. Next: **take-over** (a
  visible window, the brain disconnected while the owner drives, hand-back by restart). It is being
  designed and gets a design review before code. Then: acting with approvals, sign-in by take-over
  for low-value sites only, media. Ideas from an
  earlier project of the owner's only; own code.
- Postponed, conditional: Marathi–English support (Phase 2). The owner's condition: the base assistant must keep working properly. Marathi support is optional and may only be added if it doesn't slow the English path (latency and GPU placement measured before and after); otherwise it stays out.
- Check the full voice loop and the wake word on a real microphone (checks are scheduled with the owner), and the owner's visible check of the app window.
- Next, in this order: the day resolver, memory (server and UI in flight, see "Resume here"), the owner's visible check of the app, then wake device control after a few days of real-room telemetry.

In order, unless the owner changes it:

1. Timers, alarms and reminders (the Home card is merged; the server part is first in the reviewer's queue, not merged).
2. Memory ("remember that...").
3. A morning brief. It needs calendar and mail, so the parked send-guard question is asked
   before the mail part.
4. Media control for whatever is playing.
5. Questions about the owner's own files (local search).
6. Scheduled routines.
7. "What's on my screen" with a vision model.
8. Computer helpers: open apps and sites, dictation, local meeting and voice-note
   transcription, clipboard summary.
9. Web search with a spoken summary (through the assistant's own browser).
10. Calculation tools.
11. Speaker ID with per-person profiles.
12. Lists.
13. Proactive reminders.
14. MCP connectors with approvals, bot templates, and multi-step tasks with a plan.
15. An activity log, "forget the last 5 minutes", and a visible everything-is-local status.

Four items on this list handle very private data. Each gets a short design note and a review before
any code, like the wake word and the browser: questions about the owner's files, "what's on my
screen", dictation and meeting or voice-note transcription together with the clipboard helper, and
speaker profiles. Each note says which folders or screens, what is stored and for how long, the
visible sign, and how to turn it off.

## Pending doc follow-ups

- The held items under "Resume here" (decisions, `team.md`, private `HOW_TO_RUN.md`).

## Ideas from an earlier project

Ideas from an earlier project of the owner's: read-only, no copying (owner's decision, 2026-11-14; copying was first allowed, then withdrawn the same evening). Nothing was copied.

## Waiting on the owner

- Docker install (sudo) for Home Assistant, then the HA account and `HA_TOKEN` in `.env`
  (postponed by the owner; device work comes later).
- **Which sites the owner would sign the assistant in to** (for the later sign-in phase).
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


- **Before Gmail work merges (Phase 4):** is the narrow send-guard exception acceptable, or must
  the rule be absolute? The owner parked this on 2026-11-14; the exception stays as reviewed
  until then. Primary asks again at that gate.

## How to resume if the manager session is new

1. Read this file, `plan.md`, `decisions.md`, newest `devlog/`.
2. `git fetch && git worktree list && git branch -a` to see every branch.
3. Ask each session (by name) for a status report; update the table above.
4. Continue the flow: review → fix → merge in the order given.
