from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sasha.config import Settings
from sasha.pipeline.llm import OllamaError
from tests.server_fakes import OWN_ORIGIN, FakeSTT, FakeTTS, Harness


@pytest.fixture
def h(settings: Settings, tmp_path: Path) -> Harness:
    return Harness(settings, tmp_path)


@pytest.fixture
def client(h: Harness) -> TestClient:
    return h.client()


def test_health_reports_persona_from_config_and_no_voice(h: Harness, client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["assistant"]["name"] == h.settings.assistant.name
    assert body["assistant"]["pronouns"]["subject"] == h.settings.assistant.pronouns.subject
    assert body["profile"] == h.settings.profile.name
    assert body["default_bot"] == "main"
    assert body["voice"] == {"stt": False, "tts": False, "tts_languages": []}
    assert body["limits"] == {"max_sockets": 4}  # ServerConfig default


def test_health_voice_true_when_stages_configured(settings: Settings, tmp_path: Path) -> None:
    client = Harness(settings, tmp_path, stt=FakeSTT(), tts=FakeTTS()).client()
    voice = client.get("/api/health").json()["voice"]
    assert voice == {"stt": True, "tts": True, "tts_languages": ["en"]}


def test_models(h: Harness, client: TestClient) -> None:
    assert client.get("/api/models").json()["models"][0] == {"name": "big-model", "size_gb": 6.6}


def test_models_when_ollama_down(h: Harness, client: TestClient) -> None:
    def down() -> list[dict[str, object]]:
        raise OllamaError("Can't reach Ollama")

    h.deps.list_models = down
    r = client.get("/api/models")
    assert r.status_code == 502 and r.json() == {"error": "Can't reach Ollama"}


def test_main_bot_is_built_in(h: Harness, client: TestClient) -> None:
    bots = client.get("/api/bots").json()["bots"]
    assert len(bots) == 1
    main = bots[0]
    assert main["id"] == "main" and main["builtin"] is True
    assert main["name"] == h.settings.assistant.name
    assert main["model"] == "big-model"
    assert main["connectors"] == ["fake"]  # fixed default, not "all connectors"


def test_bot_create_edit_delete(client: TestClient) -> None:
    r = client.post("/api/bots", json={"name": "Chef", "system_prompt": "You cook."})
    assert r.status_code == 201
    bot = r.json()
    assert bot["id"].startswith("chef-") and bot["builtin"] is False
    assert bot["model"] == "big-model" and bot["connectors"] == []

    r = client.patch(f"/api/bots/{bot['id']}", json={"model": "other", "connectors": ["fake"]})
    assert r.json()["model"] == "other" and r.json()["connectors"] == ["fake"]
    assert len(client.get("/api/bots").json()["bots"]) == 2

    assert client.delete(f"/api/bots/{bot['id']}").json() == {"ok": True}
    assert client.delete(f"/api/bots/{bot['id']}").status_code == 404
    assert len(client.get("/api/bots").json()["bots"]) == 1


def test_bot_validation_errors(client: TestClient) -> None:
    assert client.post("/api/bots", json={}).json() == {"error": "name: required"}
    r = client.post("/api/bots", json={"name": "x", "connectors": ["nope"]})
    assert r.status_code == 400 and "nope" in r.json()["error"]
    r = client.post("/api/bots", json={"name": "x", "secret": 1})
    assert r.status_code == 400
    for ctype in ("text/plain", "text/plain;application/json", "application/jsonx"):
        r = client.post("/api/bots", content=b'{"name": "x"}', headers={"content-type": ctype})
        assert r.status_code == 415, ctype
    r = client.post(
        "/api/bots",
        content=b'{"name": "x"}',
        headers={"content-type": "Application/JSON; charset=utf-8"},
    )
    assert r.status_code == 201
    assert client.patch("/api/bots/../etc", json={"name": "x"}).status_code == 404


def test_main_bot_cannot_be_renamed_or_deleted(client: TestClient) -> None:
    r = client.patch("/api/bots/main", json={"name": "Other"})
    assert r.status_code == 400 and "assistant.yaml" in r.json()["error"]
    assert client.delete("/api/bots/main").status_code == 400
    r = client.patch("/api/bots/main", json={"model": "m2", "connectors": []})
    assert r.json()["model"] == "m2" and r.json()["connectors"] == []


def test_bots_persist_in_state_dir(h: Harness, client: TestClient) -> None:
    client.post("/api/bots", json={"name": "Chef"})
    assert (h.settings.paths.state_dir / "bots.json").is_file()
    again = h.client().get("/api/bots").json()["bots"]
    assert [b["name"] for b in again][1] == "Chef"


def test_connectors_list_and_status(client: TestClient) -> None:
    conns = {c["id"]: c for c in client.get("/api/connectors").json()["connectors"]}
    assert conns["fake"]["status"] == "ok" and conns["fake"]["tools"] == ["fake_echo"]
    assert conns["fake"]["secret_env"] == ["FAKE_TOKEN"]
    assert conns["other"]["status"] == "needs_setup"


def test_connector_update_and_test(h: Harness, client: TestClient) -> None:
    r = client.put("/api/connectors/fake", json={"enabled": False, "settings": {"greeting": "yo"}})
    assert r.json()["enabled"] is False and r.json()["status"] == "off"
    assert r.json()["settings"] == {"greeting": "yo"}
    assert (h.settings.paths.state_dir / "connectors.json").is_file()
    assert client.post("/api/connectors/fake/test").json() == {"ok": True, "message": "pong"}
    r = client.post("/api/connectors/other/test")
    assert r.json() == {"ok": False, "message": "not set up"}


def test_connector_update_errors(client: TestClient) -> None:
    assert client.put("/api/connectors/nope", json={"enabled": True}).status_code == 404
    assert client.put("/api/connectors/fake", json={"enabled": "yes"}).status_code == 400
    assert client.put("/api/connectors/fake", json={"settings": {"x": 1}}).status_code == 400
    assert client.put("/api/connectors/fake", json={"token": "x"}).status_code == 400


def test_unknown_api_route_is_json_404(client: TestClient) -> None:
    r = client.get("/api/nope")
    assert r.status_code == 404 and "error" in r.json()


# --- security --------------------------------------------------------------------


def test_foreign_host_header_refused(h: Harness) -> None:
    """DNS rebinding: a page on evil.example resolving to 127.0.0.1."""
    client = TestClient(h.client().app, base_url="http://evil.example:8765")
    r = client.get("/api/health")
    assert r.status_code == 403 and r.json() == {"error": "Host not allowed"}


def test_cross_site_post_refused(client: TestClient) -> None:
    r = client.post("/api/bots", json={"name": "x"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/bots", json={"name": "x"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    r = client.post("/api/bots", json={"name": "x"}, headers={"origin": OWN_ORIGIN})
    assert r.status_code == 201


def test_localhost_names_allowed(h: Harness) -> None:
    for base in ("http://localhost:8765", "http://[::1]:8765"):
        assert TestClient(h.client().app, base_url=base).get("/api/health").status_code == 200


def test_static_files_served(h: Harness, tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<p>face</p>", encoding="utf-8")
    client = h.client(web_dir=web)
    assert client.get("/").text == "<p>face</p>"
    assert client.get("/api/health").json()["ok"] is True


def test_placeholder_page_has_no_persona_name(h: Harness) -> None:
    from sasha.server.app import WEB_DIR

    page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert h.settings.assistant.name not in page
    assert h.client(web_dir=WEB_DIR).get("/").status_code == 200
