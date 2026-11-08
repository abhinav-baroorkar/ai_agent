"""LLM stage: the `LLM` protocol and an Ollama implementation (HTTP, non-streaming)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from sasha.config import LLMConfig

Message = dict[str, Any]
"""Chat message in Ollama's format: {"role": ..., "content": ..., ...}."""

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_NS_PER_MS = 1_000_000


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


class OllamaError(RuntimeError):
    pass


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

    @property
    def model_id(self) -> str:
        return self._model

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._client.post(path, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise OllamaError(
                f"Can't reach Ollama at {self.host}. Is `ollama serve` running?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(f"Ollama timed out after {self.config.timeout_s}s") from exc
        if resp.status_code == 404:
            raise OllamaError(f"Model '{self._model}' not found. Run: ollama pull {self._model}")
        if resp.is_error:
            raise OllamaError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
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
        message = body.get("message") or {}
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
        resp = self._client.get("/api/tags")
        resp.raise_for_status()
        return [str(m.get("name")) for m in resp.json().get("models", [])]
