from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from sasha.config import LLMConfig
from sasha.pipeline.llm import LLM, OllamaError, OllamaLLM

CFG = LLMConfig(model="qwen3:8b", num_ctx=2048, temperature=0.1, think=False, keep_alive="5m")


def _llm(handler: Any) -> OllamaLLM:
    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaLLM(CFG, host="http://ollama.test", client=client)


def test_implements_protocol() -> None:
    assert isinstance(_llm(lambda r: httpx.Response(200, json={})), LLM)


def test_chat_sends_payload_and_parses_tool_calls() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_state", "arguments": {"entity_id": "light.a"}}},
                        {"function": {"name": "list_devices", "arguments": '{"domain": "light"}'}},
                    ],
                },
                "eval_count": 20,
                "eval_duration": 500_000_000,
                "load_duration": 1_000_000,
            },
        )

    tools = [{"type": "function", "function": {"name": "get_state"}}]
    resp = _llm(handler).chat([{"role": "user", "content": "hi"}], tools=tools)

    assert seen["model"] == "qwen3:8b"
    assert seen["stream"] is False
    assert seen["think"] is False
    assert seen["keep_alive"] == "5m"
    assert seen["options"] == {"num_ctx": 2048, "temperature": 0.1}
    assert seen["tools"] == tools
    assert [c.name for c in resp.tool_calls] == ["get_state", "list_devices"]
    assert resp.tool_calls[1].arguments == {"domain": "light"}
    assert resp.stats["tokens_per_s"] == 40.0
    assert resp.stats["eval_ms"] == 500.0


def test_think_blocks_are_stripped() -> None:
    body = {"message": {"role": "assistant", "content": "<think>hmm</think>\nHello!"}}
    resp = _llm(lambda r: httpx.Response(200, json=body)).chat([])
    assert resp.content == "Hello!"
    assert resp.tool_calls == []


def test_missing_model_gives_pull_hint() -> None:
    llm = _llm(lambda r: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(OllamaError, match="ollama pull qwen3:8b"):
        llm.chat([])


def test_connection_error_is_friendly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(OllamaError, match="ollama serve"):
        _llm(handler).chat([])


def test_model_version_uses_digest() -> None:
    tags = {"models": [{"name": "qwen3:8b", "digest": "0123456789abcdef"}]}
    assert _llm(lambda r: httpx.Response(200, json=tags)).model_version() == "qwen3:8b@0123456789ab"


def test_model_version_falls_back_to_name() -> None:
    assert _llm(lambda r: httpx.Response(500)).model_version() == "qwen3:8b"
