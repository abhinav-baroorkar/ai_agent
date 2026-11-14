# Brain API contract v0 (server and UI both build against this)

Owner of this file: Primary. Staff SDE copies it to `AI_reference/api-contract.md`
in the server branch. To change it, ask Primary; don't change it on your own.

The brain is one local server. Default bind `127.0.0.1:8765`. The same server
serves the web UI (static files from `AI_Agent/src/sasha/avatar/web/`) at `/`.
Later, other devices (satellites) use this same protocol over Wi-Fi with a
per-device token; v0 has no auth because it only listens on localhost.

## HTTP (JSON)

| Method + path | Returns / does |
| --- | --- |
| `GET /api/health` | `{ok, assistant:{name, pronouns}, profile, default_bot, voice:{stt:bool, tts:bool}}` |
| `GET /api/models` | `{models:[{name, size_gb}]}` from Ollama |
| `GET /api/bots` | `{bots:[Bot]}` |
| `POST /api/bots` | create a Bot, returns it |
| `PATCH /api/bots/{id}` | edit a Bot |
| `DELETE /api/bots/{id}` | delete (the built-in main bot can't be deleted) |
| `GET /api/connectors` | `{connectors:[Connector]}` |
| `PUT /api/connectors/{id}` | `{enabled?, settings?}` ; secrets are never returned |
| `POST /api/connectors/{id}/test` | `{ok, message}` connectivity check |

`Bot` = `{id, name, model, system_prompt, connectors:[connector ids], builtin:bool}`.
The built-in bot has id `main`; its name and prompt come from `assistant.yaml`.

`Connector` = `{id, kind, name, enabled, status:"ok"|"off"|"needs_setup"|"error",
detail, tools:[tool names], settings:{...non-secret}, secret_env:[env var names]}`.
v0 kinds: `home_assistant` (built in). Later: `mcp` (any Model Context Protocol
server), `gmail`, `calendar`. A connector that needs a secret tells the UI which
`.env` variable to fill in; the UI never asks the user to type a secret.

Errors: HTTP 4xx/5xx with `{error: "plain message"}`.

## WebSocket `/ws?bot=<id>` (one conversation)

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
- If voice is not available (`health.voice.stt` or `.tts` false), text still works:
  the server answers `audio_start` with an `error`, and sends no `tts_*` frames.
- The UI draws the mouth from the loudness of the speech frames it plays.
