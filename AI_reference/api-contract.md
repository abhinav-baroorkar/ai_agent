# Brain API contract v0.2 (server, UI and listener all build against this)

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
  question is also spoken (`tts_*`). The next segment within `timeout_s` seconds
  (counted from the end of that speech) is read only as yes or no; anything else, or
  nothing, means no.
- `{type:"pause_capture", reason:"speaking"}` / `{type:"resume_capture"}`: some
  socket on this server is speaking (plus 300 ms after its `tts_end`). The listener
  closes its microphone until `resume_capture`. Segments that arrive meanwhile get
  `wake{heard:false}`.

Wake turns (enforced by the server for every turn on a listener socket, whatever
the `voice` bot's connectors say):
- Only `list_devices` and `get_state` while `wake_device_control` is false.
- With it true: `call_service` too, except `switch.*` (never). One light, fan or media
  player acts directly. `entity_id:"all"`, any scene, or more than 3 distinct entities
  changed in one turn ask `confirm` first; a "no" refuses the rest of the turn's
  device changes. `volume_level` is capped at 0.6. At most 6 service calls per minute
  from wake turns.
- 3 s cool-down after any turn on this server before the next wake is accepted.

## Changes
- v0.1 (2026-11-14): `speak` query parameter; one conversation per socket stated;
  `PUT /api/connectors/{id}` returns the Connector; `pronouns` is an object;
  `voice.tts_languages` added.
- v0.1.1 (2026-11-14): `GET /api/health` adds `limits:{max_sockets}`; `GET /api/models`
  lists only chat-capable models, each `{name, size_gb, tools:bool}`.
- v0.2 (2026-11-14): wake word. Listener socket (`wake=1`) with the reserved slot;
  `audio_start.wake`; `listener_state`, `listener_stats`; `wake`, `armed_expired`,
  `confirm`, `pause_capture`, `resume_capture`; built-in `voice` bot and
  `Bot.wake_device_control`; health `wake.available` and `limits.reserved_sockets`.
- Planned for v0.3 (not built yet): `POST /api/connectors` and
  `DELETE /api/connectors/{id}` to add and remove MCP connectors from Settings.
