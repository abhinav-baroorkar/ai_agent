from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from sasha.config import LLMConfig
from sasha.pipeline.llm import LLMCancelled, OllamaError, OllamaLLM, StreamingLLM

CFG = LLMConfig(model="qwen3.5:9b", think=False)


def _llm(handler: Any) -> OllamaLLM:
    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaLLM(CFG, host="http://ollama.test", client=client)


def _ndjson(*chunks: dict[str, Any]) -> bytes:
    return b"".join(json.dumps(c).encode() + b"\n" for c in chunks)


DONE = {
    "model": "qwen3.5:9b",
    "done": True,
    "message": {"role": "assistant", "content": ""},
    "eval_count": 20,
    "eval_duration": 400_000_000,
}


def test_streams_deltas_and_returns_full_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        body = _ndjson(
            {"message": {"role": "assistant", "content": "Hel"}},
            {"message": {"role": "assistant", "content": "lo"}},
            {"message": {"role": "assistant", "content": "", "thinking": "hmm"}},
            DONE,
        )
        return httpx.Response(200, content=body)

    llm = _llm(handler)
    assert isinstance(llm, StreamingLLM)
    pieces: list[str] = []
    out = llm.chat_stream([{"role": "user", "content": "hi"}], None, pieces.append)
    assert seen["stream"] is True and "tools" not in seen
    assert pieces == ["Hel", "lo"]
    assert out.content == "Hello" and out.tool_calls == []
    assert out.stats["tokens_per_s"] == 50.0
    assert "thinking" not in out.raw_message


def test_stream_collects_tool_calls() -> None:
    call = {"function": {"name": "get_state", "arguments": {"entity_id": "light.a"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        chunk = {"message": {"role": "assistant", "content": "", "tool_calls": [call]}}
        return httpx.Response(200, content=_ndjson(chunk, DONE))

    out = _llm(handler).chat_stream([], [{"type": "function"}], lambda _: None)
    assert [c.name for c in out.tool_calls] == ["get_state"]
    assert out.raw_message["tool_calls"] == [call]


def test_stream_cancel_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_ndjson({"message": {"content": "a"}}, DONE))

    with pytest.raises(LLMCancelled):
        _llm(handler).chat_stream([], None, lambda _: None, should_stop=lambda: True)


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx.Response(404, text="model not found"), "ollama pull qwen3.5:9b"),
        (httpx.Response(500, text="boom"), "Ollama error 500"),
        (httpx.Response(200, content=b"not json\n"), "not JSON"),
        (httpx.Response(200, content=_ndjson({"error": "out of memory"})), "out of memory"),
    ],
)
def test_stream_errors(response: httpx.Response, match: str) -> None:
    with pytest.raises(OllamaError, match=match):
        _llm(lambda r: response).chat_stream([], None, lambda _: None)


def test_stream_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(OllamaError, match="Can't reach Ollama"):
        _llm(handler).chat_stream([], None, lambda _: None)


def test_list_model_info_sizes() -> None:
    body = {"models": [{"name": "qwen3.5:9b", "size": 6_591_000_000}, {"size": 1}]}
    llm = _llm(lambda r: httpx.Response(200, json=body))
    # /api/show gets the same body (no "capabilities"): unknown, so listed, tools false.
    assert llm.list_model_info() == [{"name": "qwen3.5:9b", "size_gb": 6.59, "tools": False}]
    assert llm.list_models() == ["qwen3.5:9b"]
