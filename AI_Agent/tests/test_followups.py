"""Tests for review follow-ups F2 (invalid HA_URL) and F3 (chat-capable models only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from sasha.config import HomeAssistantConfig, LLMConfig, Settings
from sasha.connectors import ConnectorRegistry, HomeAssistantConnector
from sasha.pipeline.llm import OllamaLLM
from sasha.state import JsonStore
from tests.server_fakes import WS, Harness
from tests.test_connectors import _secrets
from tests.test_server_ws import frames_until_idle, opened, types

# --- F2: a malformed HA_URL is a warning, not a dead socket ---------------------------


def test_invalid_ha_url_keeps_chat_working(
    settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    h = Harness(settings, tmp_path)
    secrets = _secrets(True).model_copy(update={"ha_url": "http://[::1:8123"})
    ha = HomeAssistantConnector(HomeAssistantConfig(), lambda: secrets)  # real client
    h.deps.connectors = h.connectors = ConnectorRegistry(
        [ha], JsonStore(h.settings.paths.state_dir / "c.json")
    )
    client = h.client()
    assert client.patch("/api/bots/main", json={"connectors": ["home_assistant"]}).is_success
    with client.websocket_connect(f"{WS}/ws") as ws:
        opened(ws)
        ws.send_json({"type": "text", "text": "hello"})
        frames = frames_until_idle(ws, after_first=False)
    assert "reply" in types(frames) and "error" not in types(frames)
    assert h.llm("big-model").seen[0][1] is False  # answered, just without HA tools
    assert "HA_URL is not a valid URL" in caplog.text
    result = client.post("/api/connectors/home_assistant/test").json()
    assert result == {"ok": False, "message": "HA_URL is not a valid URL"}


# --- F3: only models that can chat; tool support reported --------------------------------

TAGS = {
    "models": [
        {"name": "qwen3.5:9b", "size": 6_591_000_000, "digest": "aaa"},
        {"name": "nomic-embed-text:latest", "size": 274_000_000, "digest": "bbb"},
        {"name": "tiny:1b", "size": 1_000_000_000, "digest": "ccc"},
        {"name": "mystery:2b", "size": 2_000_000_000, "digest": "ddd"},
    ]
}
CAPS: dict[str, Any] = {
    "qwen3.5:9b": ["completion", "tools", "thinking"],
    "nomic-embed-text:latest": ["embedding"],
    "tiny:1b": ["completion"],
}


class FakeOllama:
    def __init__(self) -> None:
        self.shows: list[str] = []
        self.show_fails = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        if request.url.path == "/api/show":
            name = json.loads(request.content)["model"]
            self.shows.append(name)
            if self.show_fails or name not in CAPS:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"capabilities": CAPS[name]})
        return httpx.Response(404)


def _llm(fake: FakeOllama) -> OllamaLLM:
    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(fake))
    return OllamaLLM(LLMConfig(model="qwen3.5:9b"), host="http://ollama.test", client=client)


def test_models_list_only_chat_models_with_tool_flag() -> None:
    fake = FakeOllama()
    assert _llm(fake).list_model_info() == [
        {"name": "qwen3.5:9b", "size_gb": 6.59, "tools": True},
        {"name": "tiny:1b", "size_gb": 1.0, "tools": False},
        {"name": "mystery:2b", "size_gb": 2.0, "tools": False},  # unknown: listed anyway
    ]


def test_capabilities_are_cached_per_digest() -> None:
    fake = FakeOllama()
    llm = _llm(fake)
    llm.list_model_info()
    first = list(fake.shows)
    llm.list_model_info()
    # Known capabilities aren't asked again; an unknown one (failed call) is retried.
    assert fake.shows[len(first) :] == ["mystery:2b"]
    TAGS["models"][0]["digest"] = "aaa2"  # re-pulled model: new digest, asked again
    try:
        llm.list_model_info()
        assert "qwen3.5:9b" in fake.shows[len(first) + 1 :]
    finally:
        TAGS["models"][0]["digest"] = "aaa"


def test_show_failing_lists_everything() -> None:
    fake = FakeOllama()
    fake.show_fails = True
    names = [m["name"] for m in _llm(fake).list_model_info()]
    assert names == [m["name"] for m in TAGS["models"]]


def test_bot_with_embedding_model_is_refused(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings, tmp_path)
    llm = _llm(FakeOllama())
    h.deps.list_models = llm.list_model_info
    h.bots._installed_models = lambda: [m["name"] for m in llm.list_model_info()]
    client = h.client()
    assert [m["name"] for m in client.get("/api/models").json()["models"]] == [
        "qwen3.5:9b",
        "tiny:1b",
        "mystery:2b",
    ]
    r = client.post("/api/bots", json={"name": "Embed", "model": "nomic-embed-text"})
    assert r.status_code == 400 and "not an installed chat model" in r.json()["error"]
    assert client.patch("/api/bots/main", json={"model": "nomic-embed-text"}).status_code == 400
    assert client.post("/api/bots", json={"name": "Ok", "model": "tiny:1b"}).status_code == 201
