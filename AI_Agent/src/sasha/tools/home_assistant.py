"""Home Assistant tools via the REST API: list_devices, get_state, call_service.

Only three tools, so a small model picks the right one more often. Everything is
limited to the domains/services in config/tools.yaml (and optional entity allowlist).
"""

from __future__ import annotations

from typing import Any

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
        if resp.is_error:
            raise ToolError(f"Home Assistant error {resp.status_code}: {resp.text[:200]}")
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
        return not exposed or entity_id in exposed

    def _check_entity(self, entity_id: str) -> None:
        if "." not in entity_id:
            raise ToolError(f"'{entity_id}' is not an entity_id (expected domain.name)")
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
                    "name": attrs.get("friendly_name", entity_id),
                    "state": s.get("state"),
                }
            )
        return devices

    def get_state(self, entity_id: str) -> dict[str, Any]:
        self._check_entity(entity_id)
        s = self._request("GET", f"/api/states/{entity_id}") or {}
        attrs = s.get("attributes") or {}
        return {
            "entity_id": entity_id,
            "state": s.get("state"),
            "attributes": {k: v for k, v in attrs.items() if k in USEFUL_ATTRIBUTES},
        }

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | list[str],
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = self.config.allowed_services.get(domain)
        if allowed is None:
            raise ToolError(f"Domain '{domain}' is not allowed")
        if service not in allowed:
            raise ToolError(f"Service '{domain}.{service}' is not allowed. Allowed: {allowed}")

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
