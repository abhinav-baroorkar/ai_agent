from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sasha.config import Settings, available_profiles, load_settings
from tests.conftest import REPO_ROOT


def test_default_profile_loads(settings: Settings) -> None:
    assert settings.profile.name == "pc"
    assert settings.profile.llm.model
    assert settings.assistant.name
    assert settings.paths.data_dir == REPO_ROOT / "Data"


def test_both_profiles_exist_and_load() -> None:
    names = available_profiles(REPO_ROOT / "AI_Agent" / "config")
    assert {"pc", "jetson-orin-nano"} <= set(names)
    for name in names:
        assert load_settings(profile=name, root=REPO_ROOT, load_env_file=False).profile.name == name


def test_profile_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SASHA_PROFILE", "jetson-orin-nano")
    assert load_settings(root=REPO_ROOT, load_env_file=False).profile.name == "jetson-orin-nano"


def test_unknown_profile_lists_choices() -> None:
    with pytest.raises(FileNotFoundError, match="pc"):
        load_settings(profile="nope", root=REPO_ROOT, load_env_file=False)


def test_rename_via_config(tmp_path: Path) -> None:
    data = yaml.safe_load((REPO_ROOT / "AI_Agent/config/assistant.yaml").read_text())
    data.update(
        name="Juniper", pronouns={"subject": "they", "object": "them", "possessive": "their"}
    )
    custom = tmp_path / "assistant.yaml"
    custom.write_text(yaml.safe_dump(data))
    s = load_settings(assistant_path=custom, root=REPO_ROOT, load_env_file=False)
    prompt = s.assistant.render_system_prompt()
    assert "You are Juniper" in prompt
    assert "they/them" in prompt
    assert "{name}" not in prompt


def test_secrets_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HA_URL", "http://ha.test:8123")
    monkeypatch.setenv("HA_TOKEN", "abc")
    s = load_settings(root=REPO_ROOT, load_env_file=False)
    assert s.secrets.ha_url == "http://ha.test:8123"
    assert s.secrets.ha_token == "abc"
