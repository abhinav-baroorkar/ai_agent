"""Model caches stay inside the project; revisions are pinned commits."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

from sasha.config import STTConfig, TTSConfig
from sasha.pipeline.model_cache import (
    OutsideProjectError,
    ensure_project_hf_env,
    hf_cache_dir,
    project_root,
    require_inside,
)
from tests.conftest import REPO_ROOT

HF_VARS = ("HF_HOME", "HF_HUB_CACHE", "HF_XET_CACHE", "HF_ASSETS_CACHE")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A private copy of os.environ without HF variables, restored after the test."""
    env = {k: v for k, v in os.environ.items() if k not in HF_VARS}
    monkeypatch.setattr(os, "environ", env)
    return env


def test_project_root_from_worktree() -> None:
    main = Path("/srv/proj")
    assert project_root(main / "Dependencies" / "worktrees" / "feat-x") == main
    assert project_root(main) == main


def test_require_inside() -> None:
    root = Path("/srv/proj")
    assert require_inside(root / "Data" / "x", root, "x") == root / "Data" / "x"
    with pytest.raises(OutsideProjectError, match="outside the project"):
        require_inside(Path("/srv/other/hf"), root, "$HF_HOME")
    with pytest.raises(OutsideProjectError):
        require_inside(root / ".." / "elsewhere", root, "x")


def test_ensure_sets_defaults_under_main_checkout(clean_env: dict[str, str]) -> None:
    worktree = Path("/srv/proj/Dependencies/worktrees/feat-x")
    home = ensure_project_hf_env(worktree)
    assert home == Path("/srv/proj/Dependencies/hf")
    assert clean_env["HF_HOME"] == "/srv/proj/Dependencies/hf"
    assert clean_env["HF_XET_CACHE"] == "/srv/proj/Dependencies/hf/xet"
    assert hf_cache_dir(worktree) == Path("/srv/proj/Dependencies/hf/hub")


@pytest.mark.parametrize("var", HF_VARS)
def test_ensure_refuses_outside_paths(clean_env: dict[str, str], var: str) -> None:
    clean_env[var] = str(Path.home() / ".cache" / "huggingface")
    with pytest.raises(OutsideProjectError, match=var):
        ensure_project_hf_env(Path("/srv/proj"))


def test_ensure_refuses_if_hf_already_imported_elsewhere(
    clean_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = types.SimpleNamespace(HF_HOME="/home/someone/.cache/huggingface")
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", fake)
    with pytest.raises(OutsideProjectError, match="already imported"):
        ensure_project_hf_env(Path("/srv/proj"))


def test_hf_constants_land_in_project_in_a_fresh_process() -> None:
    """With HF variables unset, every huggingface_hub cache path ends up in the project."""
    if importlib.util.find_spec("huggingface_hub") is None:
        pytest.skip("voice extra not installed")
    code = (
        "import json, sys; from pathlib import Path\n"
        "from sasha.pipeline.model_cache import ensure_project_hf_env\n"
        "ensure_project_hf_env(Path(sys.argv[1]))\n"
        "import huggingface_hub.constants as c\n"
        "print(json.dumps({n: str(getattr(c, n)) for n in sys.argv[2:] if hasattr(c, n)}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in HF_VARS and k != "XDG_CACHE_HOME"}
    out = subprocess.run(
        [sys.executable, "-c", code, str(REPO_ROOT), *HF_VARS],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    paths = json.loads(out.stdout.strip().splitlines()[-1])
    project = project_root(REPO_ROOT)
    assert "HF_XET_CACHE" in paths
    for name, value in paths.items():
        assert Path(value).resolve().is_relative_to(project), f"{name}={value}"


@pytest.mark.parametrize("model", [STTConfig, TTSConfig])
@pytest.mark.parametrize("revision", ["main", "1939ad2a", "A" * 40, "v1.0"])
def test_revisions_must_be_full_commit_hashes(model: type, revision: str) -> None:
    with pytest.raises(ValidationError):
        model(revision=revision)


def test_stt_revision_default_is_pinned() -> None:
    assert len(STTConfig().revision) == 40
    assert STTConfig().max_seconds == 60
    assert TTSConfig().max_chars == 4000
