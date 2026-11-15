# Brain API contract v0.3 (server, UI and listener all build against this)

Owner of this file: Primary. Staff SDE copies it to `AI_reference/api-contract.md`
in the server branch. To change it, ask Primary; don't change it on your own.

The brain is one local server. Default bind `127.0.0.1:8765`. The same server
serves the web UI (static files from `AI_Agent/src/sasha/avatar/web/`) at `/`.
Later, other devices (satellites) use this same protocol over Wi-Fi with a
per-device token; v0 has no auth because it only listens on localhost.

## HTTP (JSON)

| Method + path | Returns / does |
| --- | --- |
| `GET /api/health` | `{ok, assistant:{name, pronouns:{subject, object, possessive}}, profile, default_bot, voice:{stt:bool, tts:bool, tts_languages:["en"]}, wake:{available:bool}, limits:{max_sockets, reserved_sockets}}` |
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
wake_device_control:bool}`.
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
  - `heard:false, command:false`: not a wake (or dropped: busy, speaking, cool-down).
    Nothing else about the segment is ever sent, logged or stored.
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
- Planned for v0.4 (not built yet): `POST /api/connectors` and
  `DELETE /api/connectors/{id}` to add and remove MCP connectors from Settings.
