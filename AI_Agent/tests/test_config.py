from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sasha.config import Settings, available_profiles, load_settings, normalize_host
from tests.conftest import REPO_ROOT


def test_default_profile_loads(settings: Settings) -> None:
    assert settings.profile.name == "laptop"
    assert settings.profile.llm.model
    assert settings.assistant.name
    assert settings.paths.data_dir == REPO_ROOT / "Data"


def test_both_profiles_exist_and_load() -> None:
    names = available_profiles(REPO_ROOT / "AI_Agent" / "config")
    assert {"laptop", "pc", "jetson-orin-nano"} <= set(names)
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
    assert s.secrets.ha_token is not None
    assert s.secrets.ha_token.get_secret_value() == "abc"
    assert "abc" not in repr(s) and "abc" not in str(s.model_dump())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:11434", "http://localhost:11434"),
        ("0.0.0.0", "http://127.0.0.1:11434"),
        ("0.0.0.0:11500", "http://127.0.0.1:11500"),
        ("127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("ollama.lan", "http://ollama.lan:11434"),
        ("https://proxy.example/", "https://proxy.example"),
    ],
)
def test_ollama_host_normalised(raw: str, expected: str) -> None:
    assert normalize_host(raw) == expected


def test_temp_paths_stay_inside_the_checkout(tmp_path: Path) -> None:
    from tests.conftest import PYTEST_TMP, REPO_ROOT

    assert tmp_path.resolve().is_relative_to(PYTEST_TMP.resolve())
    assert PYTEST_TMP.resolve().is_relative_to((REPO_ROOT / "Data").resolve())


def test_stale_temp_folders_are_removed_but_not_fresh_or_linked(tmp_path: Path) -> None:
    import os
    import time

    from tests.conftest import _clean_stale

    root, outside = tmp_path / "root", tmp_path / "outside"
    old, fresh, mine = root / "old", root / "fresh", root / "mine"
    for d in (old, fresh, mine, outside):
        d.mkdir(parents=True)
    (root / "link").symlink_to(outside)
    stamp = time.time() - 2 * 86400
    for d in (old, mine, root / "link", outside):
        os.utime(d, (stamp, stamp), follow_symlinks=False)
    _clean_stale(root, mine, repo=tmp_path)
    assert not old.exists() and fresh.exists() and mine.exists()
    assert outside.exists() and (root / "link").is_symlink()


def test_stale_cleanup_does_nothing_when_base_resolves_outside_the_repo(tmp_path: Path) -> None:
    import os
    import time

    from tests.conftest import _clean_stale

    repo, outside = tmp_path / "repo", tmp_path / "outside"
    (repo / "Data").mkdir(parents=True)
    old, mine = outside / "old", outside / "mine"
    for d in (old, mine):
        d.mkdir(parents=True)
    stamp = time.time() - 2 * 86400
    os.utime(old, (stamp, stamp))
    (repo / "Data" / "pytest-tmp").symlink_to(outside)
    _clean_stale(repo / "Data" / "pytest-tmp", mine, repo=repo)
    assert old.exists() and mine.exists()
