# Brain API contract v0.4 (server, UI and listener all build against this)

Owner of this file: Primary. Staff SDE copies it to `AI_reference/api-contract.md`
in the server branch. To change it, ask Primary; don't change it on your own.

The brain is one local server. Default bind `127.0.0.1:8765`. The same server
serves the web UI (static files from `AI_Agent/src/sasha/avatar/web/`) at `/`.
Later, other devices (satellites) use this same protocol over Wi-Fi with a
per-device token; v0 has no auth because it only listens on localhost.

## HTTP (JSON)

| Method + path | Returns / does |
| --- | --- |
| `GET /api/health` | `{ok, assistant:{name, pronouns:{subject, object, possessive}}, profile, default_bot, voice:{stt:bool, tts:bool, tts_languages:["en"]}, wake:{available:bool}, limits:{max_sockets, reserved_sockets, browser_sockets}}` |
| `GET /api/models` | `{models:[{name, size_gb, tools:bool}]}` from Ollama: only models that can chat; `tools` says whether the model supports tool calls |
| `GET /api/bots` | `{bots:[Bot]}` |
| `POST /api/bots` | create a Bot, returns it |
| `PATCH /api/bots/{id}` | edit a Bot |
| `DELETE /api/bots/{id}` | delete (the built-in bots `main` and `voice` can't be deleted) |
| `GET /api/connectors` | `{connectors:[Connector]}` |
| `PUT /api/connectors/{id}` | body `{enabled?, settings?}`; returns the updated Connector; secrets are never returned |
| `POST /api/connectors/{id}/test` | `{ok, message}` connectivity check |
| `GET /api/timers` | `{now, timers:[Timer]}` (v0.3; missed and done items are kept 7 days) |
| `DELETE /api/timers/{id}` | cancel a scheduled item, or remove a missed or done one; `{ok:true}`. A ringing or pending item answers 409: end it with dismiss |
| `POST /api/timers/{id}/dismiss` | stop a ringing or pending item; returns the Timer |
| `POST /api/timers/{id}/snooze` | a ringing or pending item, or a missed timer or reminder (not a missed alarm: 409); body `{minutes: 1–60}` (the UI sends 5, 10 or 15); returns the Timer |
| `POST /api/timers/{id}/say` | speak this item's text now (the owner tapped it); `{ok:true}` |

`Bot` = `{id, name, model, system_prompt, connectors:[connector ids], builtin:bool,
wake_device_control:bool, style}`.
- `style` (v0.4.4): how the bot's orb breathes, one of `glow`, `ember`, `tide`, `gale`,
  `storm`, `mist`, `bloom`, `stone`; default `glow` (also for bots saved before styles).
  Settable on create and PATCH for user bots, and on PATCH for `main` (only its style:
  its name and prompt stay locked). `voice` has no style of its own: it returns
  `main`'s, and a PATCH with `style` answers 400. An unknown value answers 400.
- Built-in `main`: its name and prompt come from `assistant.yaml`.
- Built-in `voice`: used by wake-word turns. Same prompt as `main`; its name is the
  assistant's name plus " (wake word)". Its model and connectors default to `main`'s
  and are editable.
- `wake_device_control` is false by default. It can be set (`PATCH /api/bots/voice
  {wake_device_control:true}`) only on `voice`; any other bot answers 400. While it is
  false, wake turns are read-only (see "Wake turns" below).

`health.wake.available` is true when the server has a speech-to-text stage.
`limits.max_sockets` is the UI's own budget of `/ws` connections;
`limits.reserved_sockets` (1) is one extra slot that only a listener socket can use.
`limits.browser_sockets` (1) is a separate slot for the browser live view (`/ws/browser`, v0.4).

`Connector` = `{id, kind, name, enabled, status:"ok"|"off"|"needs_setup"|"error",
detail, tools:[tool names], settings:{...non-secret}, secret_env:[env var names]}`.
v0 kinds: `home_assistant` (built in). Later: `mcp` (any Model Context Protocol
server), `gmail`, `calendar`. A connector that needs a secret tells the UI which
`.env` variable to fill in; the UI never asks the user to type a secret.

Errors: HTTP 4xx/5xx with `{error: "plain message"}`.

## WebSocket `/ws?bot=<id>&speak=0|1` (one conversation)

Each socket is its own conversation with its own history, even for the same bot.
`speak=1` asks the server to also speak every answer (the Home face uses it);
`speak=0`, the default, sends text only (bot chat tabs use it), so no speech is
synthesised for nothing.

Text frames are JSON with a `type`. Binary frames are raw audio:
16 kHz, mono, signed 16-bit little-endian PCM, about 20 ms per frame.

Client → server:
- `{type:"text", text}`: a typed message
- `{type:"audio_start", sample_rate:16000}`, then binary mic frames, then `{type:"audio_stop"}`
- `{type:"cancel"}`: stop the current reply (and speech)
- `{type:"reset"}`: forget the conversation

Server → client:
- `{type:"state", state:"idle"|"listening"|"thinking"|"speaking"}`
- `{type:"transcript", text, final:bool}`: what the brain heard
- `{type:"tool", name, ok:bool}`: a tool ran
- `{type:"reply_delta", text}`: streamed piece of the answer
- `{type:"reply", text, tool_calls:[{name, ok}], timing:{total_ms, tokens_per_s}}`: final answer
- `{type:"tts_start", sample_rate}`, then binary speech frames, then `{type:"tts_end"}`
- `{type:"error", message}`

Rules:
- The server always sends a `state` before and after each phase, ending in `idle`.
- Speech is sent only on sockets opened with `speak=1`. If the answer's language is
  not in `health.voice.tts_languages`, the server sends the text and no `tts_*` frames.
- If voice is not available (`health.voice.stt` or `.tts` false), text still works:
  the server answers `audio_start` with an `error`, and sends no `tts_*` frames.
- The UI draws the mouth from the loudness of the speech frames it plays.

## Listener socket `/ws?wake=1&speak=1` (wake word, v0.2)

One listener (the `sasha listen` process) per server. It uses the reserved slot,
so it never takes one of the UI's `max_sockets`; a second listener gets close 1013.
The slot is claimed by the `wake=1` flag alone (local-trust model; satellites will
need a token). A listener socket always uses the `voice` bot (`bot` must be absent
or `voice`, else close 1008). It doesn't accept `text` frames (`error`).

Each speech segment the listener's local gate lets through is sent as
`{type:"audio_start", sample_rate:16000, wake:true}`, binary frames, `{type:"audio_stop"}`
(at most 8 s; longer gets `error` and is dropped).

Client → server (listener only):
- `{type:"listener_state", paused:bool}`: send on connect and on every change. While
  paused the server refuses wake audio (`error`) and drops its keep-alive override.
- `{type:"listener_stats", gate_drops:int}`: segments the local gate dropped since
  the last report (counts only, for telemetry).

Server → client (listener only):
- `{type:"wake", heard:bool, command:bool, armed_s?:number}`, after every segment:
  - `heard:false, command:false, reason`: not a wake (or dropped: busy, speaking,
    cool-down). `reason` is the outcome label only (`rejected_no_name`,
    `rejected_low_conf`, `dropped_busy`, …; v0.4.3), so the listener can say why.
    Nothing else about the segment (no text) is ever sent, logged or stored.
  - `heard:true, command:true`: the name was followed by a command (or the segment
    filled an armed window); a normal spoken turn follows (`transcript` with the
    command only, `state`, `reply_delta`, `reply`, `tts_*`).
  - `heard:true, command:false, armed_s:5`: the name alone; the next segment within
    `armed_s` seconds is the command, without the name.
- `{type:"armed_expired"}`: the armed window ended without a segment.
- `{type:"confirm", action, timeout_s}`: a device change needs a spoken yes. The
  question is also spoken (`tts_*`). The listener then opens its microphone for
  exactly one segment and sends it as a normal `audio_start {wake:true}` segment.
  The window counts `timeout_s` from the end of the question's *playback* plus the
  300 ms tail (speech frames go out faster than real time; the server estimates the
  end from `tts_start` and the audio it sent). An answer that arrives a little early
  is accepted too. Expect short answer segments here (from about 0.2 s). The server reads that segment only as yes or no (its text is
  treated like other wake audio: not logged, not stored) and answers:
- `{type:"confirm_result", accepted:bool, reason:"yes"|"no"|"timeout"|"unclear"}`.
  Only a clear yes accepts; anything else is a no (`unclear` when it was neither).
  On `timeout` the server sends it without waiting for a segment. The turn then
  continues with normal `state` frames: acting on yes, saying it did nothing otherwise.
- `{type:"pause_capture", reason:"speaking"}` / `{type:"resume_capture"}`: some
  socket on this server is speaking (plus 300 ms after its `tts_end`). The listener
  closes its microphone until `resume_capture`. Segments that arrive meanwhile get
  `wake{heard:false}`.

The listener keeps its microphone closed while a turn runs (from `wake{heard:true,
command:true}` until the final `state:"idle"`), except in the one-segment `confirm`
window. The server drops (`wake{heard:false}`) any other segment during a turn.
`health.wake.available` is false unless a speech-to-text stage is loaded.

Wake turns (enforced by the server for every turn on a listener socket, whatever
the `voice` bot's connectors say):
- Only `list_devices` and `get_state` while `wake_device_control` is false.
- With it true: `call_service` too, except `switch.*` (never). One light, fan or media
  player acts directly. `entity_id:"all"`, any scene, or more than 3 distinct entities
  changed in one turn ask `confirm` first; a "no" refuses the rest of the turn's
  device changes. `volume_level` is capped at 0.6. At most 6 service calls per minute
  from wake turns.
- 3 s cool-down after any turn on this server before the next wake is accepted.

## Timers, alarms and reminders (v0.3)

Items are made by talking (the model's timer tools); the UI lists, counts down,
dismisses, snoozes and replays them. There is no create route in v0.3.

`Timer` = `{id, kind:"timer"|"alarm"|"reminder", label, text, due_at, repeat:
"none"|"daily"|"weekdays"|"weekends", state:"scheduled"|"ringing"|"pending"|"missed"|
"done", late_s, snoozes, created_by:"chat"|"wake"}`.
- `due_at` is ISO 8601 local time with offset; `now` (same format) comes with every
  list, so the card counts down locally from `due_at` against `now`; there are no
  per-second frames.
- `text` is the reminder's text (empty for timers and alarms); `label` is optional.
  Both are user text: show them as plain text, never as markup.
- `pending`: it came due while nothing was connected; `late_s`: how late it fired.
  When a socket connects, a pending alarm at most 10 min late rings then (`ringing`,
  tones, the generic `say`); every other pending item becomes `missed`, announced with
  the generic line (a repeating alarm moves on to its next occurrence instead).
- A `missed` timer or reminder can be snoozed (card or chat), so the card may offer
  snooze on it; a `missed` alarm can only be dismissed or removed.
- `created_by:"wake"` items are marked as voice-made on the card.

All HTTP routes above sit behind the same Host, Origin and JSON content-type checks
as every other route. `dismiss` and `say` may be sent with no body at all; any body
that is sent must be JSON (`415` otherwise).

WebSocket, server → every socket:
- `{type:"timers", now, timers:[Timer]}`: on connect, and after every change (create,
  cancel, snooze, dismiss, removal, any state change). A snapshot always follows
  `timer_stopped`, so the UI takes real values (such as the new `due_at` after a
  snooze) from it.
- `{type:"timer_ringing", id, kind, created_by, say}`: an item rings. `say` is the
  plain-text sentence being spoken. For late and pending items it is only the generic
  line (e.g. "You have 2 missed reminders.") and never contains the label or text;
  their text is spoken only on `POST …/say`. The UI shows `say` as given and never
  builds its own sentence from `text`.
- `{type:"timer_stopped", id, how:"dismissed"|"snoozed"|"cancelled"|"timeout"}`.
Exactly one socket speaks a ring: the Home socket (`speak=1`) if open, else the
listener. The announcement and the repeating tone (every 10 s; up to 2 min, alarms 5
min) arrive as ordinary `tts_start` / speech frames / `tts_end` on that socket, so the
brain-wide `pause_capture` / `resume_capture` closes the listener's microphone for each.

Client → server (any socket): `{type:"timer_dismiss", id}`, `{type:"timer_snooze",
id, minutes}`.

Listener only, while an item rings on the listener:
- `{type:"stop_window", id, timeout_s}`: sent after a tone's playback has ended (after
  `resume_capture`), at most 8 s, until the next tone. The listener may open its
  microphone for one short segment, only if capture is allowed anyway, showing
  listening, and sends it as a normal `audio_start {wake:true}` segment. It closes the
  window at once on `timer_stopped` for that id (the server closes its side too).
- `{type:"stop_result", id, accepted:bool, action:"dismiss"|"snooze"|null}`: the
  segment counted only if it is at most 2 words and starts with exactly `stop`,
  `dismiss` or `snooze`. Its text is discarded like other wake audio. A bare `snooze`
  works at most 3 times per item.

Wake turns: the timer tools work without `wake_device_control`, within limits (5
created per 10 min, 10 voice-made items active). A wake turn can't cancel or snooze an
item made in a chat without the spoken `confirm`, except what is ringing now, on any
socket: "<name>, stop" stops (or snoozes) every ringing item with no yes. Bots with a
`browser` connector get no timer tools.

## Browser live view (v0.4)

The window can watch the assistant's own browser and stop it. Watching only: the
owner can't click or type into the page through the window in v0.4 (take-over for
sign-ins and bot challenges comes later). Page content is untrusted: it reaches the
window only as JPEG pictures and, for tabs, as a hostname and a short title shown as
plain text.

HTTP (same Host and Origin checks as every route; loopback clients only; a browser
request with `Sec-Fetch-Site` other than `same-origin` or `none` gets 403):

| Method + path | Returns / does |
| --- | --- |
| `GET /api/browser` | `BrowserState` (below). There is no picture route: frames go only over `/ws/browser` |
| `POST /api/browser/stop` | stop the browser now (Chrome and its proxy); `{ok:true}`, also when it wasn't running. No body needed; a body that is sent must be JSON |

`BrowserState` = `{enabled:bool, running:bool, agent:"idle"|"working", holder:string|null,
holder_bot:{id, name}|null, tabs:[{tab:int, site:string, title:string, current:bool}],
guard:{allowed:int, refused:{<category>:int}, background_refused:int}}`.
- `enabled`: the browser connector's switch in Settings, nothing more. Whether a given
  bot may browse is up to the UI to work out from that bot's `connectors` list.
- `agent`: `working` from the first browser step of a turn until that whole turn ends
  (not per tool call); `holder` is an opaque id of that conversation, or null. A
  `browser_state` is pushed the moment `agent` or `holder` changes. `holder_bot` is the bot of that
  conversation: its id and its own name from the bot store (text the owner set, never
  page text), or null when nobody holds the browser or the bot is gone.
- `tabs`: numbered as the model sees them. `site` is the hostname only, never a full
  URL; `title` is page text. Before sending, the server strips control and invisible
  format characters from both and caps `title` at 80 characters and `site` at 255.
  Show both with `textContent`, never as markup.
- `guard`: counts from the filtering proxy since the browser started (0 when stopped).
  `allowed`: connections let through. `refused`: refusals of hosts that one of the
  agent's pages asked for (the page or a link on it tried an address the guard doesn't
  allow), by category. `background_refused`: everything else refused, which is Chrome's
  own background traffic (update, account and similar calls), not anything the page did.

WebSocket `/ws/browser` (one viewer):
- Same Host and Origin checks as `/ws`, and an `Origin` header is required (the
  window's own); a socket from a non-loopback peer or without `Origin` is closed with
  1008. It has its own slot, `health.limits.browser_sockets` (1): it never takes one
  of the UI's `max_sockets` or the listener's reserved slot. A second viewer gets close
  1013; retry after a second.
- Server → window:
  - `{type:"browser_state", ...BrowserState}`: on connect and whenever it changes.
  - `{type:"browser_frame", tab, seq, w, h, jpeg}`: `jpeg` is base64. Sent only while
    this socket is watching, at most about 5 a second, and only one at a time: the
    next frame comes only after the window acknowledges this one. Frames over 512 KB
    are dropped. Show it only as `<img src="data:image/jpeg;base64,...">` (the CSP
    already allows `img-src data:`); never as HTML.
  - `{type:"browser_stopped", reason:"idle"|"owner"|"brain_stopping"|"crashed"}`,
    followed by a `browser_state` with `running:false`.
  - `{type:"error", message}`.
- Window → server:
  - `{type:"browser_watch", on:bool}`: start or stop the frames (nothing is captured
    while nobody watches). Watching survives the browser stopping and starting again:
    frames resume when it is next used.
  - `{type:"browser_ack", seq}`: after drawing frame `seq`.
  - `{type:"browser_stop"}`: the same as `POST /api/browser/stop`; works at any time,
    even in the middle of a page load or a turn. The turn's next browser step then
    fails with "The owner stopped the browser" and the next use starts it again.
- When no browser is running: `running:false`, no frames. Suggested panel text: "The
  browser isn't running. It starts when you ask the assistant to look something up."
  With `enabled:false`: "The browser is off. Turn it on in Settings." Keep the Stop
  control visible (disabled while not running). The server keeps no last frame.
- Nothing about URLs, sites, titles or frames goes into logs or telemetry: only counts
  (frames sent and dropped, viewers, stops).

## Memory (v0.6)

Notes the owner asked the assistant to remember, across days (design:
`Dependencies/team/memory-design.md`). Notes are made and forgotten by talking (the
model's `remember` / `forget` tools, checked by the server against the owner's own
words); the UI lists them, keeps or deletes voice-made ones, marks a note "ok to say
aloud", deletes any, and forgets a time window or everything.

`Note` = `{id, text, created_at, source:"chat"|"window"|"wake", bot_id, lang,
status:"active"|"pending", say_aloud:bool}`.
- `created_at` is epoch seconds (UTC). `text` is the owner's own words as said, first
  person ("I take the 8:10 bus"; at most 300 characters): show it as plain text, never
  as markup. `bot_id` is the bot whose conversation made it: take its name from
  `/api/bots`.
- `pending`: made on a wake turn (anyone in earshot). It is in no prompt until kept;
  the UI shows it as "added by voice" with keep and delete. Keeping it makes it
  `active` and `say_aloud: true`.
- `say_aloud`: wake turns (answered aloud to the room) see only these notes.
- `Bot.memory: bool`: on for the built-in bots, off for new bots (settable with PATCH
  and on create). A bot with a `browser` connector never gets memory, whatever this
  says.

HTTP (all behind the usual Host, Origin and JSON content-type checks; a POST/PATCH
body must be JSON, `415` otherwise):
- `GET /api/memory` → `{notes:[Note], count, max}`, newest first.
- `POST /api/memory/{id}/keep` (body `{}`) → `Note`. `404` unknown id; `409` if the note
  isn't waiting (only a voice-made note can be kept, so a stale click never makes a
  private note readable on wake turns).
- `PATCH /api/memory/{id}` `{say_aloud: bool}` (nothing else) → `Note`. `409` on a
  pending note (keep it first).
- `DELETE /api/memory/{id}` → `{ok:true}`.
- `POST /api/memory/forget` `{minutes: 1..1440}` → `ForgetReport`: every note made in
  the last N minutes (waiting ones too) and every open conversation's turns from that
  window (Home, the listener, every chat), so the model can't quote them any more.
  `{everything: true}` → `ForgetReport` with `turns: {}`: all notes, the file rewritten
  empty (conversations keep their turns). The page asks the owner to confirm before
  sending either.
- `ForgetReport` = `{count, items:[{id, text}], turns:{place: n}}`: exactly what went.
  `place` is `"home"` (the Home window), `"listener"` (wake turns) or a bot id (that
  bot's chat); a place with no turns removed is left out.

WebSocket, server → every socket:
- `{type:"memory", notes:[Note], count, max}`: on connect and after every change
  (made, kept, marked, deleted, forgotten).
- `{type:"forgot", since_s}`: to every socket, after a time window was forgotten (by
  talking, "forget the last 5 minutes", or from the Memory screen). The server already
  dropped every conversation's turns from the last `since_s` seconds; each page drops
  its own on-screen turns from that window too.

Server lines (from `profile.memory.lines`, never the model):
- a claimed save or forget that no tool made gets "I didn't save that." / "Nothing was
  forgotten.";
- a note that was kept is shown back: "Noted: I take the 8:10 bus." (spoken: long notes
  just "Noted.");
- a forget by talking is answered by the server from the counts ("Forgot 1 note and 2
  earlier turns."), never with a note's text;
- "forget everything" by talking always shows "Forget all your notes? Say yes to
  confirm."; only a plain yes in the very next message clears them;
- a password, PIN, code, combination or long number is refused with "I don't store
  passwords, PINs, codes or long numbers.": that reply replaces the model's on every
  path, wake turns included (held, so the secret is never shown or spoken), and the
  history keeps the owner's messages with the secret and every number masked.
A wake turn can forget only voice-made notes; the rest, a time window and everything
are for the screen. By talking, a note is named by the owner's own words ("forget the
note about the bus"), never by an id.

## Changes
- v0.1 (2026-11-14): `speak` query parameter; one conversation per socket stated;
  `PUT /api/connectors/{id}` returns the Connector; `pronouns` is an object;
  `voice.tts_languages` added.
- v0.1.1 (2026-11-14): `GET /api/health` adds `limits:{max_sockets}`; `GET /api/models`
  lists only chat-capable models, each `{name, size_gb, tools:bool}`.
- v0.2 (2026-11-14): wake word. Listener socket (`wake=1`) with the reserved slot;
  `audio_start.wake`; `listener_state`, `listener_stats`; `wake`, `armed_expired`,
  `confirm`, `confirm_result`, `pause_capture`, `resume_capture`; built-in `voice` bot and
  `Bot.wake_device_control`; health `wake.available` and `limits.reserved_sockets`.
- v0.3 (2026-11-15): timers, alarms and reminders: `/api/timers` routes; `timers`,
  `timer_ringing`, `timer_stopped`, `stop_window`, `stop_result`; `timer_dismiss`,
  `timer_snooze`.
- v0.4 (2026-11-15): browser live view: `GET /api/browser`,
  `POST /api/browser/stop`; `/ws/browser` with `browser_state`, `browser_frame`,
  `browser_stopped`, `browser_watch`, `browser_ack`, `browser_stop`; health
  `limits.browser_sockets`; `BrowserState.holder_bot`.
- v0.4.1 (2026-11-15): `GET /api/browser/frame` removed (review L1): frames reach
  the window only over `/ws/browser`. `agent` is `working` for the whole turn and pushed
  at once; `guard.refused` counts only the pages' own refusals, the rest is
  `background_refused`.
- v0.4.3 (2026-11-16): `wake` misses carry `reason` (a label, never text). The wake
  phrase is "hey" plus the name (assistant.yaml), not the name alone.
- v0.4.4 (2026-11-16): `Bot.style` (the orb's breathing style), default `glow`.
- v0.6 (2026-11-16, proposed with feat/memory-server): memory: `/api/memory` routes;
  `memory` and `forgot` frames; `Bot.memory`.
- Planned for v0.5 (not built yet): `POST /api/connectors` and
  `DELETE /api/connectors/{id}` to add and remove MCP connectors from Settings.
