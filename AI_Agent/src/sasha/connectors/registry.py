"""All connectors plus their stored state (`Data/state/connectors.json`).

Stored per connector: `enabled` and non-secret `settings`. The last test result is
kept in memory only, so a restart clears an old "error".
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sasha.connectors.base import Connector, ConnectorError, Status, TestResult
from sasha.state import JsonStore
from sasha.tools import ToolRegistry

log = logging.getLogger(__name__)


class ConnectorRegistry:
    def __init__(self, connectors: Iterable[Connector], store: JsonStore) -> None:
        self._connectors: dict[str, Connector] = {}
        for c in connectors:
            if c.id in self._connectors:
                raise ValueError(f"Duplicate connector id: {c.id}")
            self._connectors[c.id] = c
        self.store = store
        self._last_test: dict[str, TestResult] = {}

    @property
    def ids(self) -> list[str]:
        return list(self._connectors)

    def get(self, connector_id: str) -> Connector:
        try:
            return self._connectors[connector_id]
        except KeyError:
            raise ConnectorError(f"Unknown connector '{connector_id}'") from None

    def _state(self, connector: Connector) -> tuple[bool, dict[str, Any]]:
        saved = self.store.load().get(connector.id) or {}
        enabled = saved.get("enabled", connector.default_enabled())
        try:
            settings = connector.validate_settings(dict(saved.get("settings") or {}))
        except ConnectorError:  # a stale or hand-edited file must not break the server
            log.warning("Ignoring invalid stored settings for connector %s", connector.id)
            settings = connector.default_settings()
        return bool(enabled), settings

    def status(self, connector_id: str) -> tuple[Status, str]:
        connector = self.get(connector_id)
        enabled, _ = self._state(connector)
        setup = connector.setup()
        if not setup.ready:
            return "needs_setup", setup.detail
        if not enabled:
            return "off", "Turned off."
        last = self._last_test.get(connector_id)
        if last and not last.ok:
            return "error", last.message
        return "ok", last.message if last else setup.detail

    def view(self, connector_id: str) -> dict[str, Any]:
        """The API's `Connector` object. Contains no secret values."""
        connector = self.get(connector_id)
        enabled, settings = self._state(connector)
        status, detail = self.status(connector_id)
        return {
            "id": connector.id,
            "kind": connector.kind,
            "name": connector.name,
            "enabled": enabled,
            "status": status,
            "detail": detail,
            "tools": connector.tool_names(settings),
            "settings": settings,
            "secret_env": list(connector.secret_env),
        }

    def all(self) -> list[dict[str, Any]]:
        return [self.view(cid) for cid in self._connectors]

    def update(
        self, connector_id: str, enabled: bool | None, settings: dict[str, Any] | None
    ) -> dict[str, Any]:
        connector = self.get(connector_id)
        with self.store.lock:
            data = self.store.load()
            entry = dict(data.get(connector_id) or {})
            if enabled is not None:
                entry["enabled"] = enabled
            if settings is not None:
                current = dict(entry.get("settings") or {})
                entry["settings"] = connector.validate_settings({**current, **settings})
            data[connector_id] = entry
            self.store.save(data)
        self._last_test.pop(connector_id, None)  # settings changed: old result is stale
        return self.view(connector_id)

    def test(self, connector_id: str) -> TestResult:
        connector = self.get(connector_id)
        _, settings = self._state(connector)
        result = connector.test(settings)
        self._last_test[connector_id] = result
        return result

    def tools_for(self, connector_ids: Iterable[str]) -> tuple[ToolRegistry, list[str]]:
        """Tools from the given connectors that are enabled and set up, plus warnings."""
        registry = ToolRegistry()
        warnings: list[str] = []
        for cid in connector_ids:
            connector = self._connectors.get(cid)
            if connector is None:
                warnings.append(f"Connector '{cid}' no longer exists")
                continue
            enabled, settings = self._state(connector)
            if not enabled or not connector.setup().ready:
                continue
            try:
                for tool in connector.build_tools(settings):
                    registry.register(tool)
            except (ConnectorError, ValueError) as exc:
                warnings.append(f"{connector.name}: {exc}")
        return registry, warnings
