"""The built-in Home Assistant connector, wrapping the existing HA tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from sasha.config import HomeAssistantConfig, Secrets
from sasha.connectors.base import ConnectorError, SetupCheck, TestResult
from sasha.tools import Tool, ToolError
from sasha.tools.home_assistant import (
    _ENTITY_ID_RE,
    OPT_IN_DOMAINS,
    REQUEST_FAILED,
    TIMED_OUT,
    TOKEN_REJECTED,
    UNREACHABLE,
    HomeAssistantClient,
    build_home_assistant_tools,
)

ClientFactory = Callable[[str, str, HomeAssistantConfig], HomeAssistantClient]

TOOL_NAMES = ["list_devices", "get_state", "call_service"]
# What the UI may change. `allowed_services` stays in tools.yaml on purpose: widening
# what the assistant may control is a deliberate config edit, not a settings click.
EDITABLE = {"timeout_s", "exposed_entities"}
# Settings can only narrow what tools.yaml exposes: `exposed_entities` here is a
# subset filter (empty = no extra narrowing). Switches can't be added from Settings,
# because they are often garage relays or door strikes: that stays a file edit.
MAX_EXPOSED = 500


def _test_message(exc: ToolError) -> str:
    """A fixed message for the settings screen. Never includes HA_URL or HA text."""
    text = str(exc)
    if text in (UNREACHABLE, TIMED_OUT, REQUEST_FAILED, TOKEN_REJECTED):
        return text
    if text.startswith("Not found"):
        return "Home Assistant API not found; check HA_URL"
    for word in text.split():
        if word.isdigit() and len(word) == 3:
            return f"Home Assistant error {word}"
    return "Home Assistant check failed"


class HomeAssistantConnector:
    id = "home_assistant"
    kind = "home_assistant"
    name = "Home Assistant"

    def __init__(
        self,
        base: HomeAssistantConfig,
        secrets: Callable[[], Secrets],
        client_factory: ClientFactory = HomeAssistantClient,
    ) -> None:
        self.secret_env = ["HA_URL", "HA_TOKEN"]
        self.base = base
        self._secrets = secrets
        self._client_factory = client_factory

    def default_enabled(self) -> bool:
        return self.base.enabled

    def default_settings(self) -> dict[str, Any]:
        return {"timeout_s": self.base.timeout_s, "exposed_entities": []}

    def _check_entities(self, entities: Any) -> list[str]:
        if not isinstance(entities, list) or not all(isinstance(e, str) for e in entities):
            raise ConnectorError("exposed_entities must be a list of entity ids")
        if len(entities) > MAX_EXPOSED:
            raise ConnectorError(f"exposed_entities: at most {MAX_EXPOSED} entries")
        base = set(self.base.exposed_entities)
        for entity in entities:
            if not _ENTITY_ID_RE.fullmatch(entity):
                raise ConnectorError(f"Not an entity id: {entity[:80]!r}")
            if base and entity not in base:
                raise ConnectorError(
                    f"{entity} is not exposed in tools.yaml; Settings can only narrow"
                )
            if entity.split(".", 1)[0] in OPT_IN_DOMAINS and entity not in base:
                raise ConnectorError(f"{entity}: switches can only be exposed in tools.yaml")
        return list(dict.fromkeys(entities))

    def validate_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(settings) - EDITABLE)
        if unknown:
            editable = ", ".join(sorted(EDITABLE))
            raise ConnectorError(f"Unknown setting(s): {', '.join(unknown)} (editable: {editable})")
        merged = {**self.default_settings(), **settings}
        merged["exposed_entities"] = self._check_entities(merged["exposed_entities"])
        try:
            cfg = self._config(merged)
        except ValidationError as exc:
            msg = exc.errors()[0]["msg"]
            raise ConnectorError(f"Invalid Home Assistant settings: {msg}") from None
        if not 1 <= cfg.timeout_s <= 120:
            raise ConnectorError("timeout_s must be between 1 and 120 seconds")
        return {"timeout_s": cfg.timeout_s, "exposed_entities": merged["exposed_entities"]}

    def _config(self, settings: dict[str, Any]) -> HomeAssistantConfig:
        data = self.base.model_dump()
        if "timeout_s" in settings:
            data["timeout_s"] = settings["timeout_s"]
        if settings.get("exposed_entities"):  # empty: keep tools.yaml's list as it is
            data["exposed_entities"] = list(settings["exposed_entities"])
        return HomeAssistantConfig.model_validate(data)

    def setup(self) -> SetupCheck:
        secrets = self._secrets()
        missing = [
            env
            for env, value in (("HA_URL", secrets.ha_url), ("HA_TOKEN", secrets.ha_token))
            if not value
        ]
        if missing:
            return SetupCheck(False, f"Set {' and '.join(missing)} in .env, then test again.")
        return SetupCheck(True, "Uses HA_URL and HA_TOKEN from .env.")

    def tool_names(self, settings: dict[str, Any]) -> list[str]:
        return list(TOOL_NAMES)

    def _client(self, settings: dict[str, Any]) -> HomeAssistantClient:
        secrets = self._secrets()
        if not (secrets.ha_url and secrets.ha_token):
            raise ConnectorError("Home Assistant is not set up")
        token = secrets.ha_token.get_secret_value()
        return self._client_factory(secrets.ha_url, token, self._config(settings))

    def build_tools(self, settings: dict[str, Any]) -> list[Tool]:
        return build_home_assistant_tools(self._client(settings))

    def test(self, settings: dict[str, Any]) -> TestResult:
        if not self.setup().ready:
            return TestResult(False, self.setup().detail)
        try:
            client = self._client(settings)
            client.ping()
            count = len(client.list_devices())
        except ToolError as exc:
            return TestResult(False, _test_message(exc))
        except ConnectorError as exc:
            return TestResult(False, str(exc))
        except Exception as exc:  # never raise out of a settings-screen check
            return TestResult(False, f"Check failed: {type(exc).__name__}")
        return TestResult(True, f"Connected ({count} exposed devices)")
