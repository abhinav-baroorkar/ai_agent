"""Home Assistant tools via the REST API: list_devices, get_state, call_service.

Only three tools, so a small model picks the right one more often. Everything is
limited to the domains/services in config/tools.yaml (and optional entity allowlist).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import httpx

from sasha.config import HomeAssistantConfig
from sasha.tools.base import Tool, ToolError

# Service data keys the LLM may set. Anything else is dropped.
ALLOWED_DATA_KEYS = {
    "brightness_pct",
    "brightness_step_pct",
    "color_name",
    "color_temp_kelvin",
    "rgb_color",
    "transition",
    "percentage",
    "volume_level",
}
# Domains whose entities are only exposed when listed in `exposed_entities`
# (garage relays, door strikes and alarm toggles often show up as switches).
OPT_IN_DOMAINS = {"switch"}
# HA entity ids are lowercase `domain.object_id`. Anything else (`/`, `..`, `?`) is refused,
# so a crafted id can't reach another entity or endpoint through the URL path.
_ENTITY_ID_RE = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
MAX_TEXT = 64


def clean_text(value: Any) -> Any:
    """Strip control characters/newlines from HA-supplied strings and cap their length."""
    if not isinstance(value, str):
        return value
    return _CONTROL_RE.sub(" ", value).strip()[:MAX_TEXT]


# Attributes worth showing the LLM from get_state (keeps prompts short).
USEFUL_ATTRIBUTES = {
    "friendly_name",
    "brightness",
    "color_mode",
    "color_temp_kelvin",
    "rgb_color",
    "percentage",
    "volume_level",
    "media_title",
    "unit_of_measurement",
    "device_class",
}


class HomeAssistantClient:
    def __init__(
        self,
        url: str,
        token: str,
        config: HomeAssistantConfig,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.config = config
        self._client = client or httpx.Client(
            base_url=self.url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(config.timeout_s, connect=5.0),
        )

    # --- HTTP ---------------------------------------------------------------
    def _request(self, method: str, path: str, json: Any = None) -> Any:
        try:
            resp = self._client.request(method, path, json=json)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ToolError(f"Can't reach Home Assistant at {self.url}") from exc
        except httpx.TimeoutException as exc:
            raise ToolError("Home Assistant timed out") from exc
        if resp.status_code == 401:
            raise ToolError("Home Assistant rejected the token (401). Check HA_TOKEN.")
        if resp.status_code == 404:
            raise ToolError(f"Not found in Home Assistant: {path}")
        if resp.is_error:  # body not passed on: it is untrusted text
            raise ToolError(f"Home Assistant error {resp.status_code}")
        return resp.json() if resp.content else None

    def ping(self) -> str:
        body = self._request("GET", "/api/")
        return str(body.get("message", "")) if isinstance(body, dict) else ""

    # --- Access rules -------------------------------------------------------
    def _is_exposed(self, entity_id: str) -> bool:
        domain = entity_id.split(".", 1)[0]
        if domain not in self.config.allowed_services:
            return False
        exposed = self.config.exposed_entities
        if domain in OPT_IN_DOMAINS:
            return entity_id in exposed
        return not exposed or entity_id in exposed

    def _check_entity(self, entity_id: str) -> None:
        if not isinstance(entity_id, str) or not _ENTITY_ID_RE.fullmatch(entity_id):
            raise ToolError(f"'{entity_id}' is not a valid entity_id (expected domain.name)")
        if not self._is_exposed(entity_id):
            raise ToolError(f"'{entity_id}' is not exposed to the assistant")

    # --- Tool handlers ------------------------------------------------------
    def list_devices(self, domain: str | None = None) -> list[dict[str, Any]]:
        if domain and domain not in self.config.allowed_services:
            allowed = ", ".join(self.config.allowed_services)
            raise ToolError(f"Domain '{domain}' is not allowed. Allowed: {allowed}")
        states = self._request("GET", "/api/states") or []
        devices = []
        for s in states:
            entity_id = str(s.get("entity_id", ""))
            if not self._is_exposed(entity_id):
                continue
            if domain and not entity_id.startswith(f"{domain}."):
                continue
            attrs = s.get("attributes") or {}
            devices.append(
                {
                    "entity_id": entity_id,
                    "name": clean_text(attrs.get("friendly_name", entity_id)),
                    "state": clean_text(s.get("state")),
                }
            )
        return devices

    def get_state(self, entity_id: str) -> dict[str, Any]:
        self._check_entity(entity_id)
        s = self._request("GET", f"/api/states/{quote(entity_id, safe='.')}") or {}
        attrs = s.get("attributes") or {}
        return {
            "entity_id": entity_id,
            "state": clean_text(s.get("state")),
            "attributes": {k: clean_text(v) for k, v in attrs.items() if k in USEFUL_ATTRIBUTES},
        }

    def _check_scene(self, scene_id: str) -> None:
        """Refuse a scene that touches anything the assistant couldn't control directly."""
        s = self._request("GET", f"/api/states/{quote(scene_id, safe='.')}") or {}
        members = (s.get("attributes") or {}).get("entity_id")
        if members is None:  # vendor scenes (Hue etc.) list no members
            members = []
        if not isinstance(members, list):
            raise ToolError(f"Scene '{scene_id}' has unreadable members; refused")
        bad = [
            m
            for m in members
            if not (
                isinstance(m, str)
                and _ENTITY_ID_RE.fullmatch(m)
                and not m.startswith("scene.")
                and self._is_exposed(m)
            )
        ]
        if bad:
            raise ToolError(f"Scene '{scene_id}' controls devices the assistant can't; refused")

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | list[str],
        data: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        allowed = self.config.allowed_services.get(domain)
        if allowed is None:
            raise ToolError(f"Domain '{domain}' is not allowed")
        if service not in allowed:
            raise ToolError(f"Service '{domain}.{service}' is not allowed. Allowed: {allowed}")

        if entity_id == "all" and domain == "scene":
            raise ToolError("Activate one scene at a time, not 'all'")
        if entity_id == "all":
            targets = [d["entity_id"] for d in self.list_devices(domain)]
            if not targets:
                raise ToolError(f"No exposed {domain} entities")
        else:
            targets = [entity_id] if isinstance(entity_id, str) else list(entity_id)
        for t in targets:
            self._check_entity(t)
            if not t.startswith(f"{domain}."):
                raise ToolError(f"'{t}' is not in domain '{domain}'")
            if domain == "scene":
                self._check_scene(t)

        if isinstance(data, str):  # small models often send the object as JSON text
            try:
                data = json.loads(data) if data.strip() else None
            except ValueError as exc:
                raise ToolError("data must be a JSON object") from exc
        if data is not None and not isinstance(data, dict):
            raise ToolError("data must be a JSON object")
        clean = {k: v for k, v in (data or {}).items() if k in ALLOWED_DATA_KEYS}
        changed = self._request(
            "POST", f"/api/services/{domain}/{service}", json={"entity_id": targets, **clean}
        )
        new_states = {
            str(s.get("entity_id")): s.get("state")
            for s in (changed if isinstance(changed, list) else [])
            if s.get("entity_id") in targets
        }
        return {"ok": True, "called": f"{domain}.{service}", "targets": targets, "new": new_states}


def build_home_assistant_tools(client: HomeAssistantClient) -> list[Tool]:
    domains = sorted(client.config.allowed_services)
    return [
        Tool(
            name="list_devices",
            description="List smart-home devices the assistant can see, with their current state.",
            parameters={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "enum": domains,
                        "description": "Optional device type filter, e.g. 'light'.",
                    }
                },
                "required": [],
            },
            handler=client.list_devices,
        ),
        Tool(
            name="get_state",
            description="Get the current state and key attributes of one device.",
            parameters={
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "description": "e.g. 'light.bedroom'"}
                },
                "required": ["entity_id"],
            },
            handler=client.get_state,
        ),
        Tool(
            name="call_service",
            description=(
                "Control a device, e.g. domain='light', service='turn_on', "
                "entity_id='light.bedroom', data={'brightness_pct': 50}. "
                "Use entity_id='all' for every device of that domain."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "enum": domains},
                    "service": {"type": "string", "description": "e.g. turn_on, turn_off"},
                    "entity_id": {"type": "string", "description": "entity_id or 'all'"},
                    "data": {
                        "type": "object",
                        "description": "Optional: " + ", ".join(sorted(ALLOWED_DATA_KEYS)),
                    },
                },
                "required": ["domain", "service", "entity_id"],
            },
            handler=client.call_service,
        ),
    ]
