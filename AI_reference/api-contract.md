# Brain API contract v0.1.1 (server and UI both build against this)

Owner of this file: Primary. Staff SDE copies it to `AI_reference/api-contract.md`
in the server branch. To change it, ask Primary; don't change it on your own.

The brain is one local server. Default bind `127.0.0.1:8765`. The same server
serves the web UI (static files from `AI_Agent/src/sasha/avatar/web/`) at `/`.
Later, other devices (satellites) use this same protocol over Wi-Fi with a
per-device token; v0 has no auth because it only listens on localhost.

## HTTP (JSON)

| Method + path | Returns / does |
| --- | --- |
| `GET /api/health` | `{ok, assistant:{name, pronouns:{subject, object, possessive}}, profile, default_bot, voice:{stt:bool, tts:bool, tts_languages:["en"]}, limits:{max_sockets}}` |
| `GET /api/models` | `{models:[{name, size_gb, tools:bool}]}` from Ollama: only models that can chat; `tools` says whether the model supports tool calls |
| `GET /api/bots` | `{bots:[Bot]}` |
| `POST /api/bots` | create a Bot, returns it |
| `PATCH /api/bots/{id}` | edit a Bot |
| `DELETE /api/bots/{id}` | delete (the built-in main bot can't be deleted) |
| `GET /api/connectors` | `{connectors:[Connector]}` |
| `PUT /api/connectors/{id}` | body `{enabled?, settings?}`; returns the updated Connector; secrets are never returned |
| `POST /api/connectors/{id}/test` | `{ok, message}` connectivity check |

`Bot` = `{id, name, model, system_prompt, connectors:[connector ids], builtin:bool}`.
The built-in bot has id `main`; its name and prompt come from `assistant.yaml`.

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

## Changes
- v0.1 (2026-11-14): `speak` query parameter; one conversation per socket stated;
  `PUT /api/connectors/{id}` returns the Connector; `pronouns` is an object;
  `voice.tts_languages` added.
- v0.1.1 (2026-11-14): `GET /api/health` adds `limits:{max_sockets}`; `GET /api/models`
  lists only chat-capable models, each `{name, size_gb, tools:bool}`.
- Planned for v0.2 (not built yet): `POST /api/connectors` and
  `DELETE /api/connectors/{id}` to add and remove MCP connectors from Settings.
