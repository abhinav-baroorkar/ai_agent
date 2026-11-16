# Brain API contract v0.5 (server, UI and listener all build against this)

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

Window token (v0.4.5): every `/api/browser*` route needs the header
`X-Window-Token: <token>` (401 `{error}` otherwise), and `/ws/browser` needs the
subprotocols `["brain.v1", "token.<token>"]` (close 1008 otherwise; the server accepts
with `brain.v1` only). The token is made by the brain at each start and reaches the
window only through `sasha window`, as the address fragment `#t=<token>`. The UI reads
it, keeps it in memory, and strips it from the address with `history.replaceState`. A
window opened another way (or reloaded) has no token, and its browser panel is refused.
The other routes and `/ws` don't need it yet.

Take-over (v0.5): the owner drives the assistant's browser himself, as a normal visible
Chrome window on its own profile, with no DevTools connection (the brain can't see what
he types or views). Routes, behind the token like the other `/api/browser*` ones; no
body or a JSON body:

| Method + path | Returns / does |
| --- | --- |
| `POST /api/browser/take_over` | `{ok:true}` and the switch starts; 409 `{error}` when the browser is off in Settings, there is no screen, Chrome is missing, or a take-over is already on |
| `POST /api/browser/hand_back` | `{ok:true}`; the window closes and the assistant's browser comes back (also when he simply closes the window, or after 15 minutes) |
| `POST /api/browser/stop_turn` | `{ok:true}`; "Stop it now" while a take-over waits for a running turn |

`BrowserState` gains `mode`: `assistant` (normal), `waiting` (asked, a turn is finishing),
`switching`, or `owner` (his window is open); `owner_until` (ISO time, while `owner`);
and `owner_refused` (`{category:int}`, addresses his window tried that the guard refused,
counted apart from the page's own). `POST /api/browser/stop` during a take-over hands
back. While `mode` is not `assistant`, every bot's browser step answers "The owner is
using the browser right now; try again after it's handed back". After hand-back only
the search engine's cookies are kept; his history, tabs, downloads and form entries are
forgotten, and any setting or extension he changed is undone.

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
- v0.4.5 (2026-11-16): the window token on `/api/browser*` (`X-Window-Token`) and
  `/ws/browser` (subprotocols `brain.v1` and `token.<token>`).
- v0.5 (2026-11-16): take-over: `POST /api/browser/take_over`, `/hand_back`,
  `/stop_turn`; `BrowserState.mode`, `owner_until`, `owner_refused`.
- Planned for v0.6 (not built yet): `POST /api/connectors` and
  `DELETE /api/connectors/{id}` to add and remove MCP connectors from Settings.
