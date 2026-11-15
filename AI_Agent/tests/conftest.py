from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from sasha.config import Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
# Temp dirs stay inside the project (owner's rule): never /tmp, whatever the runner.
PYTEST_TMP_ROOT = REPO_ROOT / "Data" / "pytest-tmp"
# One folder per run, so concurrent runs in one checkout can't delete each other's files.
PYTEST_TMP = PYTEST_TMP_ROOT / str(os.getpid())
STALE_AFTER_S = 24 * 3600


def _clean_stale(root: Path, keep: Path) -> None:
    """Remove folders directly under `root` that are over a day old (finished runs)."""
    base = root.resolve()
    for entry in base.iterdir():
        if entry == keep.resolve() or entry.resolve().parent != base:
            continue  # never our own folder, never anything that resolves elsewhere
        if time.time() - entry.lstat().st_mtime > STALE_AFTER_S:
            shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink()


def pytest_configure(config: pytest.Config) -> None:
    if not config.option.basetemp:
        PYTEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
        _clean_stale(PYTEST_TMP_ROOT, PYTEST_TMP)
        config.option.basetemp = PYTEST_TMP


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SASHA_PROFILE", "SASHA_ASSISTANT_CONFIG", "SASHA_ROOT", "HA_URL", "HA_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def settings() -> Settings:
    return load_settings(root=REPO_ROOT, load_env_file=False)
