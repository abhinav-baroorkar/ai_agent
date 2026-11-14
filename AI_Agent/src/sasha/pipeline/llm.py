"""LLM stage: the `LLM` protocol and an Ollama implementation (HTTP, plain and streaming)."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from sasha.config import LLMConfig

Message = dict[str, Any]
"""Chat message in Ollama's format: {"role": ..., "content": ..., ...}."""

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_NS_PER_MS = 1_000_000
# Model list (`/api/models`): Ollama is local, so connects are short. Show calls share
# one budget per list; a failed one is not retried for a minute.
TAGS_TIMEOUT = httpx.Timeout(10.0, connect=2.0)
SHOW_TIMEOUT_S = 3.0
SHOW_CONNECT_S = 1.0
SHOW_BUDGET_S = 3.0
SHOW_FAILURE_TTL_S = 60.0


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    model: str
    raw_message: Message
    stats: dict[str, float | int] = field(default_factory=dict)


@runtime_checkable
class LLM(Protocol):
    @property
    def model_id(self) -> str: ...

    def chat(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None = None
    ) -> LLMResponse: ...

    def model_version(self) -> str:
        """A string that pins the exact weights, e.g. `name@digest`."""
        ...


@runtime_checkable
class StreamingLLM(LLM, Protocol):
    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None,
        on_delta: Callable[[str], None],
        should_stop: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        """Like `chat`, but calls `on_delta` with each piece of answer text as it arrives.

        Raises `LLMCancelled` as soon as `should_stop()` returns true.
        """
        ...


class OllamaError(RuntimeError):
    pass


class ThinkFilter:
    """Drops `<think>...</think>` from streamed text, even when a tag is split across chunks.

    Text that might be the start of a tag is held back until the next chunk decides.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.inside = False
        self.pending = ""

    def feed(self, text: str) -> str:
        self.pending += text
        out: list[str] = []
        while True:
            tag = self.CLOSE if self.inside else self.OPEN
            at = self.pending.find(tag)
            if at >= 0:
                if not self.inside:
                    out.append(self.pending[:at])
                self.pending = self.pending[at + len(tag) :]
                self.inside = not self.inside
                continue
            keep = next(
                (k for k in range(len(tag) - 1, 0, -1) if self.pending.endswith(tag[:k])), 0
            )
            cut = len(self.pending) - keep
            if not self.inside:
                out.append(self.pending[:cut])
            self.pending = self.pending[cut:]
            return "".join(out)

    def flush(self) -> str:
        """End of stream: return held-back text (none if a think block never closed)."""
        rest = "" if self.inside else self.pending
        self.pending = ""
        return rest


class LLMCancelled(Exception):
    """The caller asked to stop while the answer was streaming."""


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _stats(body: dict[str, Any]) -> dict[str, float | int]:
    stats: dict[str, float | int] = {}
    for key in ("prompt_eval_count", "eval_count"):
        if isinstance(body.get(key), int):
            stats[key] = body[key]
    for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
        if isinstance(body.get(key), int):
            stats[key.replace("duration", "ms")] = round(body[key] / _NS_PER_MS, 2)
    eval_ms = stats.get("eval_ms")
    if eval_ms and "eval_count" in stats:
        stats["tokens_per_s"] = round(stats["eval_count"] / (eval_ms / 1000), 2)
    return stats


class OllamaLLM:
    """Talks to an Ollama server's `/api/chat` endpoint."""

    def __init__(
        self,
        config: LLMConfig,
        host: str = "http://localhost:11434",
        model: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.host = host.rstrip("/")
        self._model = model or config.model
        self._client = client or httpx.Client(
            base_url=self.host, timeout=httpx.Timeout(config.timeout_s, connect=5.0)
        )
        self._version: str | None = None
        self._capability_cache: dict[str, list[str]] = {}  # digest -> capabilities
        self._capability_failures: dict[str, float] = {}  # digest -> when the call failed
        self._clock: Callable[[], float] = time.monotonic

    @property
    def model_id(self) -> str:
        return self._model

    def _http_error(self, exc: httpx.HTTPError) -> OllamaError:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return OllamaError(f"Can't reach Ollama at {self.host}. Is `ollama serve` running?")
        if isinstance(exc, httpx.TimeoutException):
            return OllamaError(f"Ollama timed out after {self.config.timeout_s}s")
        # dropped connection, bad URL, protocol error...
        return OllamaError(f"Ollama request failed: {type(exc).__name__}: {exc}")

    def _check_status(self, resp: httpx.Response) -> None:
        if resp.status_code == 404:
            raise OllamaError(f"Model '{self._model}' not found. Run: ollama pull {self._model}")
        if resp.is_error:
            raise OllamaError(f"Ollama error {resp.status_code}: {resp.text[:300]}")

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise self._http_error(exc) from exc
        self._check_status(resp)
        try:
            body = resp.json()
        except ValueError as exc:
            raise OllamaError("Ollama sent a response that is not JSON") from exc
        if not isinstance(body, dict):
            raise OllamaError("Unexpected response from Ollama")
        return body

    def build_payload(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "stream": False,
            "options": {"num_ctx": self.config.num_ctx, "temperature": self.config.temperature},
        }
        if tools:
            payload["tools"] = list(tools)
        if self.config.think is not None:
            payload["think"] = self.config.think
        if self.config.keep_alive:
            payload["keep_alive"] = self.config.keep_alive
        return payload

    def chat(
        self, messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None = None
    ) -> LLMResponse:
        body = self._post("/api/chat", self.build_payload(messages, tools))
        return self._response(body, body.get("message") or {})

    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None,
        on_delta: Callable[[str], None],
        should_stop: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        """Stream `/api/chat` (`stream: true`): one JSON object per line, the last has stats."""
        payload = {**self.build_payload(messages, tools), "stream": True}
        thinking = ThinkFilter()  # deltas reach the UI already filtered
        content: list[str] = []
        calls: list[Any] = []
        last: dict[str, Any] = {}
        try:
            with self._client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.is_error:
                    resp.read()
                    self._check_status(resp)
                for line in resp.iter_lines():
                    if should_stop and should_stop():
                        raise LLMCancelled
                    if not line.strip():
                        continue
                    last = _parse_chunk(line)
                    msg = last.get("message") or {}
                    calls += msg.get("tool_calls") or []
                    if piece := str(msg.get("content") or ""):
                        content.append(piece)
                        if visible := thinking.feed(piece):
                            on_delta(visible)
                if rest := thinking.flush():
                    on_delta(rest)
        except httpx.HTTPError as exc:
            raise self._http_error(exc) from exc
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
        if calls:
            message["tool_calls"] = calls
        return self._response(last, message)

    def _response(self, body: dict[str, Any], message: dict[str, Any]) -> LLMResponse:
        calls = [
            ToolCall(
                name=str(c.get("function", {}).get("name", "")),
                arguments=_parse_arguments(c.get("function", {}).get("arguments")),
            )
            for c in message.get("tool_calls") or []
        ]
        content = _THINK_RE.sub("", str(message.get("content") or "")).strip()
        return LLMResponse(
            content=content,
            tool_calls=[c for c in calls if c.name],
            model=str(body.get("model", self._model)),
            raw_message={k: v for k, v in message.items() if k != "thinking"},
            stats=_stats(body),
        )

    def model_version(self) -> str:
        """`name@digest12` from `/api/tags`; falls back to the bare name."""
        if self._version:
            return self._version
        try:
            resp = self._client.get("/api/tags")
            resp.raise_for_status()
            for m in resp.json().get("models", []):
                if self._model in (m.get("name"), m.get("model")):
                    self._version = f"{self._model}@{str(m.get('digest', ''))[:12]}"
                    return self._version
        except (httpx.HTTPError, ValueError):
            pass
        return self._model

    def list_models(self) -> list[str]:
        return [str(m["name"]) for m in self.list_model_info()]

    def _capabilities(self, name: str, digest: str, deadline: float) -> list[str] | None:
        """Ollama's capabilities for a model ("completion", "tools", "embedding"...).

        Cached per digest (a re-pulled model gets a new digest, so it is asked again).
        None when unknown: the call failed or timed out (remembered for
        SHOW_FAILURE_TTL_S, so a busy Ollama isn't asked on every page load), this
        Ollama doesn't report capabilities, or the request's time budget is spent.
        """
        key = digest or name
        if key in self._capability_cache:
            return self._capability_cache[key]
        now = self._clock()
        failed_at = self._capability_failures.get(key)
        if failed_at is not None and now - failed_at < SHOW_FAILURE_TTL_S:
            return None
        left = deadline - now
        if left <= 0:
            return None  # budget spent: list it without asking (not a failure)
        timeout = httpx.Timeout(min(SHOW_TIMEOUT_S, left), connect=min(SHOW_CONNECT_S, left))
        try:
            resp = self._client.post("/api/show", json={"model": name}, timeout=timeout)
            resp.raise_for_status()
            caps = resp.json().get("capabilities")
        except (httpx.HTTPError, ValueError, AttributeError):
            caps = None
        if not isinstance(caps, list):
            self._capability_failures[key] = self._clock()
            return None
        self._capability_failures.pop(key, None)
        self._capability_cache[key] = [str(c) for c in caps]
        return self._capability_cache[key]

    def list_model_info(self) -> list[dict[str, Any]]:
        """`[{name, size_gb, tools}]` for every pulled model that can chat.

        Models whose capabilities lack "completion" (embedding models) are left out.
        If a model's capabilities can't be read, it is listed anyway, with tools false.
        All show calls together get SHOW_BUDGET_S, so a busy Ollama can't stall the
        route (the UI gives up after 8 s, and saving a bot calls this too).
        """
        deadline = self._clock() + SHOW_BUDGET_S
        try:
            resp = self._client.get("/api/tags", timeout=TAGS_TIMEOUT)
        except httpx.HTTPError as exc:
            raise self._http_error(exc) from exc
        self._check_status(resp)
        try:
            models = resp.json().get("models", [])
        except (ValueError, AttributeError) as exc:
            raise OllamaError("Unexpected response from Ollama") from exc
        out: list[dict[str, Any]] = []
        for m in models:
            if not (isinstance(m, dict) and m.get("name")):
                continue
            name = str(m["name"])
            caps = self._capabilities(name, str(m.get("digest") or ""), deadline)
            if caps is not None and "completion" not in caps:
                continue  # can't chat (e.g. an embedding model)
            size_gb = round(int(m.get("size") or 0) / 1e9, 2)
            out.append({"name": name, "size_gb": size_gb, "tools": bool(caps and "tools" in caps)})
        return out


def _parse_chunk(line: str) -> dict[str, Any]:
    try:
        chunk = json.loads(line)
    except ValueError as exc:
        raise OllamaError("Ollama sent a stream line that is not JSON") from exc
    if not isinstance(chunk, dict):
        raise OllamaError("Unexpected stream chunk from Ollama")
    if chunk.get("error"):
        raise OllamaError(f"Ollama error: {str(chunk['error'])[:300]}")
    return chunk
