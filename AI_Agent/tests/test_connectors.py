from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from sasha.bots import parse_fields
from sasha.config import HomeAssistantConfig, Secrets, Settings, reload_secrets
from sasha.connectors import ConnectorError, ConnectorRegistry, HomeAssistantConnector
from sasha.state import JsonStore
from sasha.tools.home_assistant import HomeAssistantClient
from tests.test_home_assistant import FakeHA

BASE = HomeAssistantConfig(allowed_services={"light": ["turn_on", "turn_off"]})
TOKEN = "tok"


def _secrets(ready: bool) -> Secrets:
    if not ready:
        return Secrets()
    return Secrets(ha_url="http://ha.test", ha_token=SecretStr(TOKEN))


def _connector(ready: bool = True, ha: FakeHA | None = None) -> HomeAssistantConnector:
    fake = ha or FakeHA()

    def factory(url: str, token: str, cfg: HomeAssistantConfig) -> HomeAssistantClient:
        client = httpx.Client(
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
            transport=httpx.MockTransport(fake),
        )
        return HomeAssistantClient(url, token, cfg, client=client)

    return HomeAssistantConnector(BASE, lambda: _secrets(ready), factory)


def _registry(tmp_path: Path, connector: HomeAssistantConnector) -> ConnectorRegistry:
    return ConnectorRegistry([connector], JsonStore(tmp_path / "connectors.json"))


def test_needs_setup_without_secrets(tmp_path: Path) -> None:
    reg = _registry(tmp_path, _connector(ready=False))
    view = reg.view("home_assistant")
    assert view["status"] == "needs_setup" and "HA_URL and HA_TOKEN" in view["detail"]
    assert view["secret_env"] == ["HA_URL", "HA_TOKEN"]
    assert reg.test("home_assistant").ok is False
    tools, _ = reg.tools_for(["home_assistant"])
    assert len(tools) == 0


def test_ready_gives_tools_and_never_returns_the_token(tmp_path: Path) -> None:
    reg = _registry(tmp_path, _connector())
    assert reg.view("home_assistant")["status"] == "ok"
    tools, warnings = reg.tools_for(["home_assistant"])
    assert tools.names == ["list_devices", "get_state", "call_service"] and warnings == []
    result = reg.test("home_assistant")
    assert result.ok and "exposed devices" in result.message
    assert TOKEN not in json.dumps(reg.all()) + result.message


def test_settings_validated_and_stored(tmp_path: Path) -> None:
    reg = _registry(tmp_path, _connector())
    view = reg.update("home_assistant", False, {"exposed_entities": ["light.bedroom"]})
    assert view["enabled"] is False and view["status"] == "off"
    assert view["settings"]["exposed_entities"] == ["light.bedroom"]
    stored = json.loads((tmp_path / "connectors.json").read_text())
    assert stored["home_assistant"]["settings"]["exposed_entities"] == ["light.bedroom"]
    assert TOKEN not in json.dumps(stored)
    for bad in ({"allowed_services": {"lock": ["unlock"]}}, {"timeout_s": 0}, {"token": "x"}):
        with pytest.raises(ConnectorError):
            reg.update("home_assistant", None, bad)


def test_failed_test_shows_error_until_settings_change(tmp_path: Path) -> None:
    class Down(FakeHA):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

    reg = _registry(tmp_path, _connector(ha=Down()))
    assert reg.test("home_assistant").ok is False
    assert reg.view("home_assistant")["status"] == "error"
    reg.update("home_assistant", True, None)
    assert reg.view("home_assistant")["status"] == "ok"


def test_corrupt_state_file_is_moved_aside(tmp_path: Path) -> None:
    (tmp_path / "connectors.json").write_text("{not json", encoding="utf-8")
    reg = _registry(tmp_path, _connector())
    assert reg.view("home_assistant")["enabled"] is True
    assert (tmp_path / "connectors.json.corrupt").is_file()


def test_unknown_connector(tmp_path: Path) -> None:
    reg = _registry(tmp_path, _connector())
    with pytest.raises(ConnectorError, match="Unknown connector"):
        reg.view("nope")
    _, warnings = reg.tools_for(["nope"])
    assert warnings == ["Connector 'nope' no longer exists"]


def test_reload_secrets_reads_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HA_URL", "http://old.test")
    assert reload_secrets(tmp_path).ha_url == "http://old.test"
    (tmp_path / ".env").write_text("HA_URL=http://new.test\nHA_TOKEN=abc\n", encoding="utf-8")
    secrets = reload_secrets(tmp_path)
    assert secrets.ha_url == "http://new.test" and secrets.ha_token is not None


def test_build_deps_uses_state_dir(settings: Settings, tmp_path: Path) -> None:
    from sasha.server.deps import build_deps

    paths = settings.paths.model_copy(update={"root": tmp_path, "data_dir": tmp_path / "Data"})
    deps = build_deps(settings.model_copy(update={"paths": paths}))
    assert deps.connectors.ids == ["home_assistant"]
    assert deps.connectors.view("home_assistant")["status"] == "needs_setup"
    assert deps.bots.get("main").connectors == ["home_assistant"]
    assert deps.stt is None and deps.tts is None
    assert deps.bots.update("main", parse_fields({"model": "m2"})).model == "m2"
    assert (tmp_path / "Data" / "state" / "bots.json").is_file()
