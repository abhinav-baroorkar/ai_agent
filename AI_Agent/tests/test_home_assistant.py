from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from sasha.config import HomeAssistantConfig
from sasha.tools import ToolError, ToolRegistry
from sasha.tools.home_assistant import HomeAssistantClient, build_home_assistant_tools

STATES = [
    {"entity_id": "light.bedroom", "state": "off", "attributes": {"friendly_name": "Bedroom"}},
    {"entity_id": "light.kitchen", "state": "on", "attributes": {"brightness": 200}},
    {"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
    {"entity_id": "sensor.temp", "state": "21", "attributes": {}},
]
CFG = HomeAssistantConfig(allowed_services={"light": ["turn_on", "turn_off"], "scene": ["turn_on"]})


class FakeHA:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.headers.get("authorization") != "Bearer tok":
            return httpx.Response(401)
        if path == "/api/":
            return httpx.Response(200, json={"message": "API running."})
        if path == "/api/states":
            return httpx.Response(200, json=STATES)
        if path.startswith("/api/states/"):
            eid = path.rsplit("/", 1)[1]
            match = [s for s in STATES if s["entity_id"] == eid]
            return httpx.Response(200, json=match[0]) if match else httpx.Response(404)
        if path.startswith("/api/services/"):
            body = json.loads(request.content)
            self.posts.append((path, body))
            return httpx.Response(
                200, json=[{"entity_id": e, "state": "on"} for e in body["entity_id"]]
            )
        return httpx.Response(404)


def _client(
    cfg: HomeAssistantConfig = CFG, token: str = "tok"
) -> tuple[HomeAssistantClient, FakeHA]:
    fake = FakeHA()
    http = httpx.Client(
        base_url="http://ha.test",
        headers={"Authorization": f"Bearer {token}"},
        transport=httpx.MockTransport(fake),
    )
    return HomeAssistantClient("http://ha.test", token, cfg, client=http), fake


def test_list_devices_only_allowed_domains() -> None:
    ha, _ = _client()
    ids = [d["entity_id"] for d in ha.list_devices()]
    assert ids == ["light.bedroom", "light.kitchen"]
    assert ha.list_devices()[0]["name"] == "Bedroom"


def test_exposed_entities_allowlist() -> None:
    cfg = CFG.model_copy(update={"exposed_entities": ["light.kitchen"]})
    ha, _ = _client(cfg)
    assert [d["entity_id"] for d in ha.list_devices()] == ["light.kitchen"]
    with pytest.raises(ToolError, match="not exposed"):
        ha.get_state("light.bedroom")


def test_get_state_filters_attributes() -> None:
    ha, _ = _client()
    assert ha.get_state("light.kitchen") == {
        "entity_id": "light.kitchen",
        "state": "on",
        "attributes": {"brightness": 200},
    }


def test_call_service_posts_and_drops_unknown_data() -> None:
    ha, fake = _client()
    out = ha.call_service("light", "turn_on", "light.bedroom", {"brightness_pct": 40, "evil": 1})
    assert fake.posts == [
        ("/api/services/light/turn_on", {"entity_id": ["light.bedroom"], "brightness_pct": 40})
    ]
    assert out["new"] == {"light.bedroom": "on"}


def test_call_service_all_expands_to_exposed() -> None:
    ha, fake = _client()
    ha.call_service("light", "turn_off", "all")
    assert fake.posts[0][1]["entity_id"] == ["light.bedroom", "light.kitchen"]


@pytest.mark.parametrize(
    ("domain", "service", "entity", "msg"),
    [
        ("lock", "unlock", "lock.front_door", "not allowed"),
        ("light", "delete", "light.bedroom", "not allowed"),
        ("light", "turn_on", "scene.x", "not in domain"),
        ("light", "turn_on", "switch.x", "not exposed"),
        ("light", "turn_on", "bedroom", "not an entity_id"),
    ],
)
def test_call_service_rejects(domain: str, service: str, entity: str, msg: str) -> None:
    ha, fake = _client()
    with pytest.raises(ToolError, match=msg):
        ha.call_service(domain, service, entity)
    assert fake.posts == []


def test_bad_token_message() -> None:
    ha, _ = _client(token="wrong")
    with pytest.raises(ToolError, match="401"):
        ha.ping()


def test_tools_through_registry() -> None:
    ha, fake = _client()
    reg = ToolRegistry(build_home_assistant_tools(ha))
    assert reg.names == ["list_devices", "get_state", "call_service"]
    assert reg.specs()[2]["function"]["parameters"]["properties"]["domain"]["enum"] == [
        "light",
        "scene",
    ]
    ok = reg.call(
        "call_service",
        {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen", "x": 1},
    )
    assert ok.ok and fake.posts
    denied = reg.call(
        "call_service", {"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"}
    )
    assert not denied.ok and "not allowed" in json.loads(denied.content)["error"]
    missing = reg.call("get_state", {})
    assert not missing.ok and "entity_id" in missing.content
    assert not reg.call("send_email", {}).ok
