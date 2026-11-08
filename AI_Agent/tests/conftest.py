from __future__ import annotations

from pathlib import Path

import pytest

from sasha.config import Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SASHA_PROFILE", "SASHA_ASSISTANT_CONFIG", "SASHA_ROOT", "HA_URL", "HA_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def settings() -> Settings:
    return load_settings(root=REPO_ROOT, load_env_file=False)
